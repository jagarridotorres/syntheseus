from __future__ import annotations

import math
from abc import abstractmethod
from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import root_validator
from pydantic.generics import GenericModel

from syntheseus.interface.bag import Bag
from syntheseus.interface.molecule import Molecule

InputType = TypeVar("InputType")
OutputType = TypeVar("OutputType")


class Prediction(GenericModel, Generic[InputType, OutputType]):
    """Reaction prediction from a model, either a forward or a backward one."""

    # Make `pydantic` accept custom types such as `Molecule` or `Bag`.
    class Config:
        arbitrary_types_allowed = True

    # The molecule that the prediction is for and the predicted output:
    input: InputType
    output: OutputType

    # Optional information that may be useful downstream:
    probability: Optional[float] = None  # Prior probability.
    log_prob: Optional[float] = None  # As above, but in log space.
    score: Optional[float] = None  # Any other score.
    reaction: Optional[str] = None  # Reaction smiles.
    rxnid: Optional[int] = None  # Template id, if applicable.
    metadata: Optional[Dict[str, Any]] = {}  # Additional metadata.

    def get_prob(self) -> float:
        if self.probability is not None:
            return self.probability
        elif self.log_prob is not None:
            return math.exp(self.log_prob)
        else:
            raise ValueError("Prediction does not have associated probability or log prob value.")

    def get_log_prob(self) -> float:
        if self.log_prob is not None:
            return self.log_prob
        elif self.probability is not None:
            return math.log(self.probability)
        else:
            raise ValueError("Prediction does not have associated log prob or probability value.")

    @root_validator()
    def check_at_most_one_source_prob(cls, values):
        if values.get("probability") is not None and values.get("log_prob") is not None:
            raise ValueError(
                "Probability can be stored as probability or log probability, not both"
            )
        return values


class PredictionList(GenericModel, Generic[InputType, OutputType]):
    """Several possible predictions."""

    # Make `pydantic` accept custom types such as `Molecule` or `Bag`.
    class Config:
        arbitrary_types_allowed = True

    input: InputType
    predictions: List[Prediction[InputType, OutputType]]
    metadata: Optional[Dict[str, Any]] = {}

    def truncated(self, num_results: int) -> PredictionList[InputType, OutputType]:
        fields = self.dict()
        fields["predictions"] = fields["predictions"][:num_results]

        return PredictionList(**fields)


class ReactionModel(Generic[InputType, OutputType]):
    """Base class for all reaction models, both backward and forward."""

    @abstractmethod
    def __call__(
        self, inputs: List[InputType], num_results: int
    ) -> List[PredictionList[InputType, OutputType]]:
        """Given a batch of inputs to the reaction model, return a batch of results.

        Args:
            inputs: Batch of inputs to the reaction model, each either a molecule or a set of
                molecules, depending on directionality.
            num_results: Number of results to return for each input in the batch. Many models may
                only be able to produce a finite number of candidate outputs, thus the returned
                lists are allowed to be shorter than `num_results`.
        """
        pass

    def get_model_info(self) -> dict[str, Any]:
        return {}

    @abstractmethod
    def is_forward(self) -> bool:
        pass

    def is_backward(self) -> bool:
        return not self.is_forward()

    def get_parameters(self):
        """Return an iterator over parameters (used for computing total parameter count).

        If accurate reporting of number of parameters during evaluation is not important, subclasses
        are free to e.g. return an empty list.
        """
        raise NotImplementedError()


# Below we define some aliases for forward and backward variants of prediction and model classes.
# Model interfaces use bags of SMILES as output type to allow for salts and disconnected components.


class BackwardReactionModel(ReactionModel[Molecule, Bag[Molecule]]):
    def is_forward(self) -> bool:
        return False


class ForwardReactionModel(ReactionModel[Bag[Molecule], Bag[Molecule]]):
    def is_forward(self) -> bool:
        return True


BackwardPrediction = Prediction[Molecule, Bag[Molecule]]
ForwardPrediction = Prediction[Bag[Molecule], Bag[Molecule]]

BackwardPredictionList = PredictionList[Molecule, Bag[Molecule]]
ForwardPredictionList = PredictionList[Bag[Molecule], Bag[Molecule]]
