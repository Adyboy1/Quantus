# This file is part of Quantus.
# Quantus is free software: you can redistribute it and/or modify it under the terms of the GNU Lesser General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
# Quantus is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more details.
# You should have received a copy of the GNU Lesser General Public License along with Quantus. If not, see <https://www.gnu.org/licenses/>.
# Quantus project URL: <https://github.com/understandable-machine-intelligence-lab/Quantus>.

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, List, Optional

import numpy as np

if TYPE_CHECKING:
    import tensorflow as tf
    import torch

import sys

from quantus.functions.normalise_func import normalise_by_average_second_moment_estimate
from quantus.functions.perturb_func import batch_uniform_noise
from quantus.helpers.enums import (
    DataType,
    EvaluationCategory,
    ModelType,
    ScoreDirection,
)
from quantus.helpers.model.model_interface import ModelInterface
from quantus.helpers.perturbation_utils import (
    make_changed_prediction_indices_func,
    make_perturb_func,
)
from quantus.helpers.warn import warn_parameterisation
from quantus.metrics.base import Metric

if sys.version_info >= (3, 8):
    from typing import final
else:
    from typing_extensions import final


@final
class RelativeRepresentationStability(Metric[List[float]]):
    """
    Relative Representation Stability leverages the stability of an explanation with respect
    to the change in the output logits.

        `RRS(x, x', ex, ex') = max \\frac{||\\frac{e_x - e_{x'}}{e_x}||_p}
        {max (||\\frac{L_x - L_{x'}}{L_x}||_p, \epsilon_{min})},`

    where `L(·)` denotes the internal model representation, e.g., output embeddings of hidden layers.

    References:
        1) Chirag Agarwal, et. al., 2022. "Rethinking stability for attribution based explanations.",
        https://arxiv.org/pdf/2203.06877.pdf

    Attributes:
        -  _name: The name of the metric.
        - _data_applicability: The data types that the metric implementation currently supports.
        - _models: The model types that this metric can work with.
        - score_direction: How to interpret the scores, whether higher/ lower values are considered better.
        - evaluation_category: What property/ explanation quality that this metric measures.
    """

    name = "Relative Representation Stability"
    data_applicability = {DataType.IMAGE, DataType.TIMESERIES, DataType.TABULAR}
    model_applicability = {ModelType.TORCH, ModelType.TF}
    score_direction = ScoreDirection.LOWER
    evaluation_category = EvaluationCategory.ROBUSTNESS

    def __init__(
        self,
        nr_samples: int = 200,
        abs: bool = False,
        normalise: bool = False,
        normalise_func: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        normalise_func_kwargs: Optional[Dict[str, ...]] = None,
        perturb_func: Optional[Callable] = None,
        perturb_func_kwargs: Optional[Dict[str, ...]] = None,
        return_aggregate: bool = False,
        aggregate_func: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        disable_warnings: bool = False,
        display_progressbar: bool = False,
        eps_min: float = 1e-6,
        default_plot_func: Optional[Callable] = None,
        layer_names: Optional[List[str]] = None,
        layer_indices: Optional[List[int]] = None,
        return_nan_when_prediction_changes: bool = True,
        **kwargs,
    ):
        """
        Parameters
        ----------
        nr_samples: int
            The number of samples iterated, default=200.
        abs: boolean
            Indicates whether absolute operation is applied on the attribution.
        normalise: boolean
            Flag stating if the attributions should be normalised
        normalise_func: callable
            Attribution normalisation function applied in case normalise=True.
        normalise_func_kwargs: dict
            Keyword arguments to be passed to normalise_func on call, default={}.
        perturb_func: callable
            Input perturbation function. If None, the default value is used, default=gaussian_noise.
        perturb_func_kwargs: dict
            Keyword arguments to be passed to perturb_func, default={}.
        return_aggregate: boolean
            Indicates if an aggregated score should be computed over all instances.
        aggregate_func: callable
            Callable that aggregates the scores given an evaluation call.
        disable_warnings: boolean
            Indicates whether the warnings are printed, default=False.
        display_progressbar: boolean
            Indicates whether a tqdm-progress-bar is printed, default=False.
        default_plot_func: callable
            Callable that plots the metrics result.
        eps_min: float
            Small constant to prevent division by 0 in relative_stability_objective, default 1e-6.
        layer_names: List[str], optional
            Names of layers, representations of which should be used for RRS computation, default = all.
        layer_indices: List[int], optional
            Indices of layers, representations of which should be used for RRS computation, default = all.
        return_nan_when_prediction_changes: boolean
            When set to true, the metric will be evaluated to NaN if the prediction changes after the perturbation is applied, default=True.
        """

        if normalise_func is None:
            normalise_func = normalise_by_average_second_moment_estimate

        super().__init__(
            abs=abs,
            normalise=normalise,
            normalise_func=normalise_func,
            normalise_func_kwargs=normalise_func_kwargs,
            return_aggregate=return_aggregate,
            aggregate_func=aggregate_func,
            default_plot_func=default_plot_func,
            display_progressbar=display_progressbar,
            disable_warnings=disable_warnings,
            **kwargs,
        )
        if perturb_func is None:
            perturb_func = batch_uniform_noise
        self._nr_samples = nr_samples
        self._eps_min = eps_min
        if layer_names is not None and layer_indices is not None:
            raise ValueError("Must provide either layer_names OR layer_indices, not both.")

        self._layer_names = layer_names
        self._layer_indices = layer_indices
        self.perturb_func = make_perturb_func(perturb_func, perturb_func_kwargs, upper_bound=0.2)
        self.changed_prediction_indices_func = make_changed_prediction_indices_func(return_nan_when_prediction_changes)

        if not self.disable_warnings:
            warn_parameterisation(
                metric_name=self.__class__.__name__,
                sensitive_params=(
                    "function used to generate perturbations 'perturb_func' and parameters passed to it 'perturb_func_kwargs'"
                    "number of times perturbations are sampled 'nr_samples'"
                    "choice which internal representations to use 'layer_names', 'layer_indices'"
                ),
                citation='Chirag Agarwal, et. al., 2022. "Rethinking stability for attribution based explanations." https://arxiv.org/pdf/2203.06877.pdf',
            )

    def __call__(  # type: ignore
        self,
        model: tf.keras.Model | torch.nn.Module,
        x_batch: np.ndarray,
        y_batch: np.ndarray,
        model_predict_kwargs: Optional[Dict[str, ...]] = None,
        explain_func: Optional[Callable] = None,
        explain_func_kwargs: Optional[Dict[str, ...]] = None,
        a_batch: Optional[np.ndarray] = None,
        device: Optional[str] = None,
        softmax: bool = False,
        channel_first: bool = True,
        batch_size: int = 64,
        **kwargs,
    ) -> List[float]:
        """
        For each image `x`:
         - Generate `num_perturbations` perturbed `xs` in the neighborhood of `x`.
         - Compute explanations `e_x` and `e_xs`.
         - Compute relative representation stability objective, find max value with respect to `xs`.
         - In practise we just use `max` over a finite `xs_batch`.

        Parameters
        ----------
        model: tf.keras.Model, torch.nn.Module
            A torch or tensorflow model that is subject to explanation.
        x_batch: np.ndarray
            4D tensor representing batch of input images
        y_batch: np.ndarray
            1D tensor, representing predicted labels for the x_batch.
        model_predict_kwargs: dict, optional
            Keyword arguments to be passed to the model's predict method.
        explain_func: callable, optional
            Function used to generate explanations.
        explain_func_kwargs: dict, optional
            Keyword arguments to be passed to explain_func on call.
        a_batch: np.ndarray, optional
            4D tensor with pre-computed explanations for the x_batch.
        device: str, optional
            Device on which torch should perform computations.
        softmax: boolean, optional
            Indicates whether to use softmax probabilities or logits in model prediction.
            This is used for this __call__ only and won't be saved as attribute. If None, self.softmax is used.
        channel_first: boolean, optional
            Indicates of the image dimensions are channel first, or channel last.
            Inferred from the input shape if None.
        batch_size: int
            The batch size to be used.
        kwargs:
            not used, deprecated
        Returns
        -------
        relative representation stability: float, np.ndarray
            float in case `return_aggregate=True`, otherwise np.ndarray of floats
        """
        return super().__call__(
            model=model,
            x_batch=x_batch,
            y_batch=y_batch,
            explain_func=explain_func,
            explain_func_kwargs=explain_func_kwargs,
            a_batch=a_batch,
            device=device,
            softmax=softmax,
            channel_first=channel_first,
            model_predict_kwargs=model_predict_kwargs,
            s_batch=None,
            batch_size=batch_size,
        )

    def relative_representation_stability_objective(
        self,
        l_x: np.ndarray,
        l_xs: np.ndarray,
        e_x: np.ndarray,
        e_xs: np.ndarray,
    ) -> np.ndarray:
        """
        Computes relative representation stabilities maximization objective
        as defined here https://arxiv.org/pdf/2203.06877.pdf by the authors.

        Parameters
        ----------
        l_x: np.ndarray
            Internal representation for x_batch.
        l_xs: np.ndarray
            Internal representation for xs_batch.
        e_x: np.ndarray
            Explanations for x.
        e_xs: np.ndarray
            Explanations for xs.

        Returns
        -------
        rrs_obj: np.ndarray
            RRS maximization objective.
        """

        num_dim = e_x.ndim
        if num_dim == 4:
            norm_function = lambda arr: np.linalg.norm(np.linalg.norm(arr, axis=(-1, -2)), axis=-1)  # noqa
        elif num_dim == 3:
            norm_function = lambda arr: np.linalg.norm(arr, axis=(-1, -2))  # noqa
        elif num_dim == 2:
            norm_function = lambda arr: np.linalg.norm(arr, axis=-1)
        else:
            raise ValueError("Relative Input Stability only supports 4D, 3D and 2D inputs (batch dimension inclusive).")

        # fmt: off
        nominator = (e_x - e_xs) / (e_x + (e_x == 0) * self._eps_min)  # prevent division by 0
        nominator = norm_function(nominator)
        # fmt: on
        denominator = l_x - l_xs
        denominator /= l_x + (l_x == 0) * self._eps_min  # prevent division by 0
        denominator = np.linalg.norm(denominator, axis=-1)
        denominator += (denominator == 0) * self._eps_min
        return nominator / denominator

    def evaluate_batch(
        self,
        model: ModelInterface,
        x_batch: np.ndarray,
        y_batch: np.ndarray,
        a_batch: np.ndarray,
        **kwargs,
    ) -> np.ndarray:
        """
        Parameters
        ----------
        model: tf.keras.Model, torch.nn.Module
            A torch or tensorflow model that is subject to explanation.
        x_batch: np.ndarray
            4D tensor representing batch of input images.
        y_batch: np.ndarray
            1D tensor, representing predicted labels for the x_batch.
        a_batch: np.ndarray, optional
            4D tensor with pre-computed explanations for the x_batch.
        kwargs:
            Unused.

        Returns
        -------
        ris: np.ndarray
            The batched evaluation results.

        """
        batch_size = x_batch.shape[0]

        # Retrieve internal representation for provided inputs.
        internal_representations = model.get_hidden_representations(x_batch, self._layer_names, self._layer_indices)

        # Prepare output array.
        rrs_batch = np.zeros(shape=[self._nr_samples, x_batch.shape[0]])

        for index in range(self._nr_samples):
            # Perturb input.
            x_perturbed = self.perturb_func(
                arr=x_batch.reshape(batch_size, -1),
                indices=np.tile(np.arange(0, x_batch[0].size), (batch_size, 1)),
            )
            x_perturbed = x_perturbed.reshape(*x_batch.shape)

            # Generate explanations for perturbed input.
            a_batch_perturbed = self.explain_batch(model, x_perturbed, y_batch)

            # Retrieve internal representation for perturbed inputs.
            internal_representations_perturbed = model.get_hidden_representations(
                x_perturbed, self._layer_names, self._layer_indices
            )

            # Compute maximization's objective.
            rrs = self.relative_representation_stability_objective(
                internal_representations,
                internal_representations_perturbed,
                a_batch,
                a_batch_perturbed,
            )
            rrs_batch[index] = rrs
            # If perturbed input caused change in prediction, then it's RRS=nan.
            changed_prediction_indices = self.changed_prediction_indices_func(model, x_batch, x_perturbed)

            if len(changed_prediction_indices) != 0:
                rrs_batch[index, changed_prediction_indices] = np.nan

        # Compute RRS.
        result = np.max(rrs_batch, axis=0)
        if self.return_aggregate:
            result = [self.aggregate_func(result)]

        return result
