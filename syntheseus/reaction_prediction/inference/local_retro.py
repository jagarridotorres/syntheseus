"""Inference wrapper for the LocalRetro model.

Paper: https://pubs.acs.org/doi/10.1021/jacsau.1c00246
Code: https://github.com/kaist-amsg/LocalRetro

The original LocalRetro code is released under the Apache 2.0 license.
Parts of this file are based on code from the GitHub repository above.
"""

import sys
from pathlib import Path
from typing import Any, List, Union

from syntheseus.interface.models import BackwardPredictionList, BackwardReactionModel
from syntheseus.interface.molecule import Molecule
from syntheseus.reaction_prediction.utils.inference import (
    get_module_path,
    get_unique_file_in_dir,
    process_raw_smiles_outputs,
)
from syntheseus.reaction_prediction.utils.misc import suppress_outputs


class LocalRetroModel(BackwardReactionModel):
    def __init__(self, model_dir: Union[str, Path], device: str = "cuda:0") -> None:
        """Initializes the LocalRetro model wrapper.

        Assumed format of the model directory:
        - `model_dir` contains the model checkpoint as the only `*.pth` file
        - `model_dir` contains the config as the only `*.json` file
        - `model_dir/data` contains `*.csv` data files needed by LocalRetro
        """

        import LocalRetro
        from LocalRetro import scripts

        # We need to hack `sys.path` because LocalRetro uses relative imports.
        sys.path.insert(0, get_module_path(LocalRetro))
        sys.path.insert(0, get_module_path(scripts))

        from LocalRetro.Retrosynthesis import load_templates
        from LocalRetro.scripts.utils import init_featurizer, load_model

        data_dir = Path(model_dir) / "data"
        self.args = init_featurizer(
            {
                "mode": "test",
                "device": device,
                "model_path": get_unique_file_in_dir(model_dir, pattern="*.pth"),
                "config_path": get_unique_file_in_dir(model_dir, pattern="*.json"),
                "data_dir": data_dir,
                "rxn_class_given": False,
            }
        )

        with suppress_outputs():
            self.model = load_model(self.args)

        [
            self.args["atom_templates"],
            self.args["bond_templates"],
            self.args["template_infos"],
        ] = load_templates(self.args)

    def get_parameters(self):
        return self.model.parameters()

    def _mols_to_batch(self, mols: List[Molecule]) -> Any:
        from dgllife.utils import smiles_to_bigraph
        from LocalRetro.scripts.utils import collate_molgraphs_test

        graphs = [
            smiles_to_bigraph(
                mol.smiles,
                node_featurizer=self.args["node_featurizer"],
                edge_featurizer=self.args["edge_featurizer"],
                add_self_loop=True,
                canonical_atom_order=False,
            )
            for mol in mols
        ]

        return collate_molgraphs_test([(None, graph, None) for graph in graphs])[1]

    def _build_batch_predictions(
        self, batch, num_results, inputs, batch_atom_logits, batch_bond_logits
    ):
        from LocalRetro.scripts.Decode_predictions import get_k_predictions
        from LocalRetro.scripts.get_edit import combined_edit, get_bg_partition

        graphs, nodes_sep, edges_sep = get_bg_partition(batch)
        start_node = 0
        start_edge = 0

        self.args["top_k"] = num_results
        self.args["raw_predictions"] = []

        for input, graph, end_node, end_edge in zip(inputs, graphs, nodes_sep, edges_sep):
            pred_types, pred_sites, pred_scores = combined_edit(
                graph,
                batch_atom_logits[start_node:end_node],
                batch_bond_logits[start_edge:end_edge],
                num_results,
            )
            start_node, start_edge = end_node, end_edge

            raw_predictions = [
                f"({pred_types[i]}, {pred_sites[i][0]}, {pred_sites[i][1]}, {pred_scores[i]:.3f})"
                for i in range(num_results)
            ]

            self.args["raw_predictions"].append([input.smiles] + raw_predictions)

        batch_predictions = []
        for idx, input in enumerate(inputs):
            # We have to `eval` the predictions as they come rendered into strings. Second tuple
            # component is empirically (on USPTO-50K test set) in [0, 1], resembling a probability,
            # but does not sum up to 1.0 (usually to something in [0.5, 2.0]).
            raw_results = list(map(eval, get_k_predictions(test_id=idx, args=self.args)[1][0]))

            if raw_results:
                raw_outputs, probabilities = zip(*raw_results)
            else:
                raw_outputs = probabilities = []

            batch_predictions.append(
                process_raw_smiles_outputs(
                    input=input,
                    output_list=raw_outputs,
                    kwargs_list=[{"probability": probability} for probability in probabilities],
                )
            )

        return batch_predictions

    def __call__(self, inputs: List[Molecule], num_results: int) -> List[BackwardPredictionList]:
        import torch
        from LocalRetro.scripts.utils import predict

        batch = self._mols_to_batch(inputs)
        batch_atom_logits, batch_bond_logits, _ = predict(self.args, self.model, batch)

        batch_atom_logits = torch.nn.Softmax(dim=1)(batch_atom_logits)
        batch_bond_logits = torch.nn.Softmax(dim=1)(batch_bond_logits)

        return self._build_batch_predictions(
            batch, num_results, inputs, batch_atom_logits, batch_bond_logits
        )
