"""This module contains the implementation of the Max-Sensitivity metric."""

# This file is part of Quantus.
# Quantus is free software: you can redistribute it and/or modify it under the terms of the GNU Lesser General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
# Quantus is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more details.
# You should have received a copy of the GNU Lesser General Public License along with Quantus. If not, see <https://www.gnu.org/licenses/>.
# Quantus project URL: <https://github.com/understandable-machine-intelligence-lab/Quantus>.

import sys
from typing import Any, Callable, Dict, List, Optional

import numpy as np
from quantus.functions import norm_func
from quantus.functions.perturb_func import batch_uniform_noise
from quantus.functions.similarity_func import difference
from quantus.helpers import asserts, warn
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
from quantus.metrics.base import Metric

if sys.version_info >= (3, 8):
    from typing import final
else:
    from typing_extensions import final


class MaxSensitivity(Metric[List[float]]):
    """
    Implementation of Max-Sensitivity by Yeh at el., 2019.

    Using Monte Carlo sampling-based approximation while measuring how explanations
    change under slight perturbation - the maximum sensitivity is captured.

    References:
        1) Chih-Kuan Yeh et al. "On the (in) fidelity and sensitivity for explanations."
        NeurIPS (2019): 10965-10976.
        2) Umang Bhatt et al.: "Evaluating and aggregating
        feature-based model explanations."  IJCAI (2020): 3016-3022.

    Attributes:
        -  _name: The name of the metric.
        - _data_applicability: The data types that the metric implementation currently supports.
        - _models: The model types that this metric can work with.
        - score_direction: How to interpret the scores, whether higher/ lower values are considered better.
        - evaluation_category: What property/ explanation quality that this metric measures.
    """

    name = "Max-Sensitivity"
    data_applicability = {DataType.IMAGE, DataType.TIMESERIES, DataType.TABULAR}
    model_applicability = {ModelType.TORCH, ModelType.TF}
    score_direction = ScoreDirection.LOWER
    evaluation_category = EvaluationCategory.ROBUSTNESS

    def __init__(
        self,
        similarity_func: Optional[Callable] = None,
        norm_numerator: Optional[Callable] = None,
        norm_denominator: Optional[Callable] = None,
        nr_samples: int = 200,
        abs: bool = False,
        normalise: bool = False,
        normalise_func: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        normalise_func_kwargs: Optional[Dict[str, Any]] = None,
        perturb_func: Optional[Callable] = None,
        lower_bound: float = 0.2,
        upper_bound: Optional[float] = None,
        perturb_func_kwargs: Optional[Dict[str, Any]] = None,
        return_aggregate: bool = False,
        aggregate_func: Optional[Callable] = None,
        default_plot_func: Optional[Callable] = None,
        disable_warnings: bool = False,
        display_progressbar: bool = False,
        return_nan_when_prediction_changes: bool = False,
        **kwargs,
    ):
        """
        Parameters
        ----------
        similarity_func: callable
            Similarity function applied to compare input and perturbed input.
            If None, the default value is used, default=difference.
        norm_numerator: callable
            Function for norm calculations on the numerator.
            If None, the default value is used, default=fro_norm
        norm_denominator: callable
            Function for norm calculations on the denominator.
            If None, the default value is used, default=fro_norm
        nr_samples: integer
            The number of samples iterated, default=200.
        normalise: boolean
            Indicates whether normalise operation is applied on the attribution, default=True.
        normalise_func: callable
            Attribution normalisation function applied in case normalise=True.
            If normalise_func=None, the default value is used, default=normalise_by_max.
        normalise_func_kwargs: dict
            Keyword arguments to be passed to normalise_func on call, default={}.
        perturb_func: callable
            Input perturbation function. If None, the default value is used,
            default=gaussian_noise.
        lower_bound: float
            The lower bound of the noise.
        upper_bound: float, optional
            The upper bound of the noise.
        perturb_func_kwargs: dict
            Keyword arguments to be passed to perturb_func, default={}.
        return_aggregate: boolean
            Indicates if an aggregated score should be computed over all instances.
        aggregate_func: callable
            Callable that aggregates the scores given an evaluation call.
        default_plot_func: callable
            Callable that plots the metrics result.
        disable_warnings: boolean
            Indicates whether the warnings are printed, default=False.
        display_progressbar: boolean
            Indicates whether a tqdm-progress-bar is printed, default=False.
        return_nan_when_prediction_changes: boolean
            When set to true, the metric will be evaluated to NaN if the prediction changes after the perturbation is applied.
        kwargs: optional
            Keyword arguments.
        """
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

        # Save metric-specific attributes.
        self.nr_samples = nr_samples

        if similarity_func is None:
            similarity_func = difference
        self.similarity_func = similarity_func

        if norm_numerator is None:
            norm_numerator = norm_func.fro_norm
        self.norm_numerator = norm_numerator

        if norm_denominator is None:
            norm_denominator = norm_func.fro_norm

        self.norm_denominator = norm_denominator
        self.perturb_func = make_perturb_func(
            perturb_func,
            perturb_func_kwargs,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
        )
        self.changed_prediction_indices_func = make_changed_prediction_indices_func(return_nan_when_prediction_changes)
        self.max_func = np.max if return_nan_when_prediction_changes else np.nanmax

        # Asserts and warnings.
        if not self.disable_warnings:
            warn.warn_parameterisation(
                metric_name=self.__class__.__name__,
                sensitive_params=(
                    "amount of noise added 'lower_bound' and 'upper_bound', the number of samples "
                    "iterated over 'nr_samples', the function to perturb the input "
                    "'perturb_func', the similarity metric 'similarity_func' as well as "
                    "norm calculations on the numerator and denominator of the sensitivity"
                    " equation i.e., 'norm_numerator' and 'norm_denominator'"
                ),
                citation=(
                    "Yeh, Chih-Kuan, et al. 'On the (in) fidelity and sensitivity for explanations"
                    ".' arXiv preprint arXiv:1901.09392 (2019)"
                ),
            )
            warn.warn_noise_zero(noise=lower_bound)

    def __call__(
        self,
        model,
        x_batch: np.ndarray,
        y_batch: np.ndarray,
        a_batch: Optional[np.ndarray] = None,
        s_batch: Optional[np.ndarray] = None,
        channel_first: Optional[bool] = None,
        explain_func: Optional[Callable] = None,
        explain_func_kwargs: Optional[Dict] = None,
        model_predict_kwargs: Optional[Dict] = None,
        softmax: Optional[bool] = False,
        device: Optional[str] = None,
        batch_size: int = 64,
        **kwargs,
    ) -> List[float]:
        """
        This implementation represents the main logic of the metric and makes the class object callable.
        It completes instance-wise evaluation of explanations (a_batch) with respect to input data (x_batch),
        output labels (y_batch) and a torch or tensorflow model (model).

        Calls general_preprocess() with all relevant arguments, calls
        () on each instance, and saves results to evaluation_scores.
        Calls custom_postprocess() afterwards. Finally returns evaluation_scores.

        Parameters
        ----------
        model: torch.nn.Module, tf.keras.Model
            A torch or tensorflow model that is subject to explanation.
        x_batch: np.ndarray
            A np.ndarray which contains the input data that are explained.
        y_batch: np.ndarray
            A np.ndarray which contains the output labels that are explained.
        a_batch: np.ndarray, optional
            A np.ndarray which contains pre-computed attributions i.e., explanations.
        s_batch: np.ndarray, optional
            A np.ndarray which contains segmentation masks that matches the input.
        channel_first: boolean, optional
            Indicates of the image dimensions are channel first, or channel last.
            Inferred from the input shape if None.
        explain_func: callable
            Callable generating attributions.
        explain_func_kwargs: dict, optional
            Keyword arguments to be passed to explain_func on call.
        model_predict_kwargs: dict, optional
            Keyword arguments to be passed to the model's predict method.
        softmax: boolean
            Indicates whether to use softmax probabilities or logits in model prediction.
            This is used for this __call__ only and won't be saved as attribute. If None, self.softmax is used.
        device: string
            Indicated the device on which a torch.Tensor is or will be allocated: "cpu" or "gpu".
        kwargs: optional
            Keyword arguments.

        Returns
        -------
        evaluation_scores: list
            a list of Any with the evaluation scores of the concerned batch.

        Examples:
        --------
            # Minimal imports.
            >> import quantus
            >> from quantus import LeNet
            >> import torch

            # Enable GPU.
            >> device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

            # Load a pre-trained LeNet classification model (architecture at quantus/helpers/models).
            >> model = LeNet()
            >> model.load_state_dict(torch.load("tutorials/assets/pytests/mnist_model"))

            # Load MNIST datasets and make loaders.
            >> test_set = torchvision.datasets.MNIST(root='./sample_data', download=True)
            >> test_loader = torch.utils.data.DataLoader(test_set, batch_size=24)

            # Load a batch of inputs and outputs to use for XAI evaluation.
            >> x_batch, y_batch = iter(test_loader).next()
            >> x_batch, y_batch = x_batch.cpu().numpy(), y_batch.cpu().numpy()

            # Generate Saliency attributions of the test set batch of the test set.
            >> a_batch_saliency = Saliency(model).attribute(inputs=x_batch, target=y_batch, abs=True).sum(axis=1)
            >> a_batch_saliency = a_batch_saliency.cpu().numpy()

            # Initialise the metric and evaluate explanations by calling the metric instance.
            >> metric = Metric(abs=True, normalise=False)
            >> scores = metric(model=model, x_batch=x_batch, y_batch=y_batch, a_batch=a_batch_saliency)
        """
        return super().__call__(
            model=model,
            x_batch=x_batch,
            y_batch=y_batch,
            a_batch=a_batch,
            s_batch=s_batch,
            custom_batch=None,
            channel_first=channel_first,
            explain_func=explain_func,
            explain_func_kwargs=explain_func_kwargs,
            softmax=softmax,
            device=device,
            model_predict_kwargs=model_predict_kwargs,
            batch_size=batch_size,
            **kwargs,
        )

    def evaluate_batch(
        self,
        model: ModelInterface,
        x_batch: np.ndarray,
        y_batch: np.ndarray,
        a_batch: np.ndarray,
        **kwargs,
    ) -> np.ndarray:
        """
        Evaluates model and attributes on a single data batch and returns the batched evaluation result.

        Parameters
        ----------
        model: ModelInterface
            A ModelInteface that is subject to explanation.
        x_batch: np.ndarray
            The input to be evaluated on an instance-basis.
        y_batch: np.ndarray
            The output to be evaluated on an instance-basis.
        a_batch: np.ndarray
            The explanation to be evaluated on an instance-basis.
        kwargs:
            Unused.

        Returns
        -------
        scores_batch: np.ndarray
            The batched evaluation results.
        """
        batch_size = x_batch.shape[0]
        a_batch = a_batch.reshape(batch_size, -1)
        similarities = np.zeros((batch_size, self.nr_samples)) * np.nan
        for step_id in range(self.nr_samples):
            # Perturb input.
            x_perturbed = self.perturb_func(
                arr=x_batch.reshape(batch_size, -1),
                indices=np.tile(np.arange(0, x_batch[0].size), (batch_size, 1)),
            )
            x_perturbed = x_perturbed.reshape(*x_batch.shape)

            changed_prediction_indices = self.changed_prediction_indices_func(model, x_batch, x_perturbed)

            for x_instance, x_instance_perturbed in zip(x_batch, x_perturbed):
                warn.warn_perturbation_caused_no_change(
                    x=x_instance,
                    x_perturbed=x_instance_perturbed,
                )

            # Generate explanation based on perturbed input x.
            a_perturbed = self.explain_batch(model, x_perturbed, y_batch)
            a_perturbed = a_perturbed.reshape(batch_size, -1)

            # Measure similarity for each instance separately.
            sensitivities = self.similarity_func(a=a_batch, b=a_perturbed)
            numerator = self.norm_numerator(a=sensitivities)
            denominator = self.norm_denominator(a=a_batch)
            similarities[:, step_id] = numerator / denominator
            similarities[changed_prediction_indices, step_id] = np.nan

        return self.max_func(similarities, axis=1)

    def custom_preprocess(
        self,
        **kwargs,
    ) -> None:
        """
        Implementation of custom_preprocess_batch.

        Parameters
        ----------
        kwargs:
            Unused.

        Returns
        -------
        None
        """
        # Additional explain_func assert, as the one in prepare() won't be
        # executed when a_batch != None.
        asserts.assert_explain_func(explain_func=self.explain_func)
