import warnings
from collections import OrderedDict

import numpy as np

from SALib.analyze import sobol, delta, fast, morris, dgsm,  ff
from tvb_epilepsy.base.utils import formal_repr, list_of_dicts_to_dicts_of_ndarrays, dict_str
from tvb_epilepsy.base.h5_model import convert_to_h5_model
from tvb.basic.logger.builder import get_logger

METHODS = ["sobol", "latin", "delta", "dgsm", "fast", "fast_sampler", "morris", "ff", "fractional_factorial"]

LOG = get_logger(__name__)

# TODO: make sensitivity_analysis_from_hypothesis() helper function

# TODO: make example-testing function __main__

# TODO: make __repr__ and prepare h5_model functions


class SensitivityAnalysisService(object):

    def __init__(self, inputs, outputs, method="delta", calc_second_order=True, conf_level=0.95):

        self._set_method(method)
        self._set_calc_second_order(calc_second_order)
        self._set_conf_level(conf_level)

        self.n_samples = []
        self.input_names = []
        self.input_bounds = []
        self.input_samples = []
        self.n_inputs = len(inputs)

        for input in inputs:

            self.input_names.append(input["name"])

            samples = np.array(input["samples"]).flatten()
            self.n_samples.append(samples.size)
            self.input_samples.append(samples)

            self.input_bounds.append(input.get("bounds", [samples.min(), samples.max()]))

        if len(self.n_samples) > 0:
            if np.all(np.array(self.n_samples) == self.n_samples[0]):
                self.n_samples = self.n_samples[0]
            else:
                raise ValueError("Not all input parameters have equal number of samples!: " + str(self.n_samples))

        self.input_samples = np.array(self.input_samples).T

        self.n_outputs = 0
        self.output_values = []
        self.output_names = []

        for output in outputs:

            if output["values"].size == self.n_samples:
                n_outputs = 1
                self.output_values.append(output["values"].flatten())
            else:
                if output["values"].shape[0] == self.n_samples:
                    self.output_values.append(output["values"])
                    n_outputs = output["values"].shape[1]
                elif output["values"].shape[1] == self.n_samples:
                    self.output_values.append(output["values"].T)
                    n_outputs = output["values"].shape[0]
                else:
                    raise ValueError("Non of the dimensions of output samples: " + str(output["values"].shape) +
                                     " matches n_samples = " + str(self.n_samples) + " !")
            self.n_outputs += n_outputs

            if n_outputs > 1 and len(output["names"]) == 1:
                self.output_names += np.array(["%s[%d]" % l for l in zip(np.repeat(output["names"][0], n_outputs),
                                                                         range(n_outputs))]).tolist()
            else:
                self.output_names += output["names"]

        if len(self.output_values) > 0:
            self.output_values = np.vstack(self.output_values)

        self.problem = {}
        self.other_parameters = {}

    def __repr__(self):

        d = {"01. Method": self.method,
             "02. Second order calculation flag": self.calc_second_order,
             "03. Confidence level": self.conf_level,
             "05. Number of inputs": self.n_inputs,
             "06. Number of outputs": self.n_outputs,
             "07. Input names": self.input_names,
             "08. Output names": self.output_names,
             "09. Input bounds": self.input_bounds,
             "10. Problem": dict_str(self.problem),
             "11. Other parameters": dict_str(self.other_parameters),
             }
        return formal_repr(self, d)

    def __str__(self):
        return self.__repr__()

    def _prepare_for_h5(self):
        h5_model = convert_to_h5_model({"method": self.method, "calc_second_order": self.calc_second_order,
                                   "conf_level": self.conf_level, "n_inputs": self.n_inputs,
                                   "n_outputs": self.n_outputs, "input_names": self.input_names,
                                   "output_names": self.output_names,
                                   "input_bounds": self.input_bounds,
                                   "problem": self.problem,
                                   "other_parameters": self.other_parameters
                                        })
        h5_model.add_or_update_metadata_attribute("EPI_Type", "HypothesisModel")
        return h5_model

    def write_to_h5(self, folder, filename=""):
        if filename == "":
            filename = self.name + ".h5"
        h5_model = self._prepare_for_h5()
        h5_model.write_to_h5(folder, filename)

    def _set_method(self, method):
        method = method.lower()
        if np.in1d(method, METHODS):
            self.method = method
        else:
            raise ValueError(
                "Method " + str(method) + " is not one of the available methods " + str(METHODS) + " !")

    def _set_calc_second_order(self, calc_second_order):
        if isinstance(calc_second_order, bool):
            self.calc_second_order = calc_second_order
        else:
            raise ValueError("calc_second_order = " + str(calc_second_order) + "is not a boolean as it should!")

    def _set_conf_level(self, conf_level):
        if isinstance(conf_level, float) and conf_level > 0.0 and conf_level < 1.0:
            self.conf_level = conf_level
        else:
            raise ValueError("conf_level = " + str(conf_level) +
                             "is not a float in the (0.0, 1.0) interval as it should!")

    def _update_parameters(self, method=None, calc_second_order=None, conf_level=None):

        if method is not None:
            self._set_method(method)

        if calc_second_order is not None:
            self._calc_set_second_order(calc_second_order)

        if conf_level is not None:
            self._set_conf_level(conf_level)


    def run(self, input_ids=None, output_ids=None, method=None, calc_second_order=None, conf_level=None, **kwargs):

        self._update_parameters(method, calc_second_order, conf_level)

        self.other_parameters = kwargs

        if input_ids is None:
            input_ids = range(self.n_inputs)

        self.problem = {"num_vars": len(input_ids),
                        "names": np.array(self.input_names)[input_ids].tolist(),
                        "bounds": np.array(self.input_bounds)[input_ids].tolist()}

        if output_ids is None:
            output_ids = range(self.n_outputs)

        n_outputs = len(output_ids)

        if self.method.lower() == "sobol":
            warnings.warn("'sobol' method requires 'saltelli' sampling scheme!")
            # Additional keyword parameters and their defaults:
            # calc_second_order (bool): Calculate second-order sensitivities (default True)
            # num_resamples (int): The number of resamples used to compute the confidence intervals (default 1000)
            # conf_level (float): The confidence interval level (default 0.95)
            # print_to_console (bool): Print results directly to console (default False)
            # parallel: False,
            # n_processors: None
            self.analyzer = lambda output: sobol.analyze(self.problem, output, calc_second_order=self.calc_second_order,
                                                         conf_level=self.conf_level,
                                                         num_resamples=self.other_parameters.get("num_resamples", 1000),
                                                         parallel=self.other_parameters.get("parallel", False),
                                                         n_processors=self.other_parameters.get("n_processors", None),
                                                  print_to_console=self.other_parameters.get("print_to_console", False))

        elif np.in1d(self.method.lower(), ["latin", "delta"]):
            warnings.warn("'latin' sampling scheme is recommended for 'delta' method!")
            # Additional keyword parameters and their defaults:
            # num_resamples (int): The number of resamples used to compute the confidence intervals (default 1000)
            # conf_level (float): The confidence interval level (default 0.95)
            # print_to_console (bool): Print results directly to console (default False)
            self.analyzer = lambda output: delta.analyze(self.problem, self.input_samples[:, input_ids], output,
                                                         conf_level=self.conf_level,
                                                         num_resamples=self.other_parameters.get("num_resamples", 1000),
                                                         print_to_console=self.other_parameters.get("print_to_console",
                                                                                                    False))

        elif np.in1d(self.method.lower(), ["fast", "fast_sampler"]):
            warnings.warn("'fast' method requires 'fast_sampler' sampling scheme!")
            # Additional keyword parameters and their defaults:
            # M (int): The interference parameter,
            #           i.e., the number of harmonics to sum in the Fourier series decomposition (default 4)
            # print_to_console (bool): Print results directly to console (default False)
            self.analyzer = lambda output: fast.analyze(self.problem, output, M=self.other_parameters.get("M", 4),
                                                        print_to_console=self.other_parameters.get("print_to_console",
                                                                                                   False))

        elif np.in1d(self.method.lower(), ["ff", "fractional_factorial"]):
            # Additional keyword parameters and their defaults:
            # second_order (bool, default=False): Include interaction effects
            # print_to_console (bool, default=False): Print results directly to console
            warnings.warn("'fractional_factorial' method requires 'fractional_factorial' sampling scheme!")
            self.analyzer = lambda output: ff.analyze(self.problem, self.input_samples[:, input_ids], output,
                                                      calc_second_order=self.calc_second_order,
                                                      conf_level=self.conf_level,
                                                      num_resamples=self.other_parameters.get("num_resamples", 1000),
                                                      print_to_console=self.other_parameters.get("print_to_console",
                                                                                                 False))


        elif self.method.lower().lower() == "morris":
            warnings.warn("'morris' method requires 'morris' sampling scheme!")
            # Additional keyword parameters and their defaults:
            # num_resamples (int): The number of resamples used to compute the confidence intervals (default 1000)
            # conf_level (float): The confidence interval level (default 0.95)
            # print_to_console (bool): Print results directly to console (default False)
            # grid_jump (int): The grid jump size, must be identical to the value passed to
            #                   SALib.sample.morris.sample() (default 2)
            # num_levels (int): The number of grid levels, must be identical to the value passed to
            #                   SALib.sample.morris (default 4)
            self.analyzer = lambda output: morris.analyze(self.problem, self.input_samples[:, input_ids], output,
                                                          conf_level=self.conf_level,
                                                          grid_jump=self.other_parameters.get("grid_jump", 2),
                                                          num_levels=self.other_parameters.get("num_levels", 4),
                                                          num_resamples=self.other_parameters.get("num_resamples",
                                                                                                  1000),
                                                          print_to_console=self.other_parameters.get("print_to_console",
                                                                                                     False))

        elif self.method.lower() == "dgsm":
            # num_resamples (int): The number of resamples used to compute the confidence intervals (default 1000)
            # conf_level (float): The confidence interval level (default 0.95)
            # print_to_console (bool): Print results directly to console (default False)
            self.analyzer = lambda output: dgsm.analyze(self.problem, self.input_samples[:, input_ids], output,
                                                        conf_level=self.conf_level,
                                                        num_resamples=self.other_parameters.get("num_resamples", 1000),
                                                        print_to_console=self.other_parameters.get("print_to_console",
                                                                                                   False))

        else:
            raise ValueError(
                "Method " + str(self.method) + " is not one of the available methods " + str(METHODS) + " !")

        output_names = []
        results = []
        for io in output_ids:
            output_names.append(self.output_names[io])
            results.append(self.analyzer(self.output_values[:, io]))

         # TODO: Adjust list_of_dicts_to_dicts_of_ndarrays to handle ndarray concatenation
        results = list_of_dicts_to_dicts_of_ndarrays(results)

        results.update({"output_names": output_names})

        return results


# Test and example function:

if __name__ == "__main__":

    import os
    from tvb_epilepsy.base.constants import DATA_CUSTOM, FOLDER_RES
    from tvb_epilepsy.custom.readers_custom import CustomReader as Reader
    from tvb_epilepsy.base.disease_hypothesis import DiseaseHypothesis
    from tvb_epilepsy.base.model_configuration_service import ModelConfigurationService
    from tvb_epilepsy.base.lsa_service import LSAService
    from tvb_epilepsy.base.helper_functions import sensitivity_analysis_pse_from_hypothesis

    # -------------------------------Reading data-----------------------------------

    data_folder = os.path.join(DATA_CUSTOM, 'Head')

    reader = Reader()

    head = reader.read_head(data_folder)

    # --------------------------Hypothesis definition-----------------------------------

    n_samples = 100

    #
    # Manual definition of hypothesis...:
    x0_indices = [20]
    x0_values = [0.9]
    e_indices = [70]
    e_values = [0.9]
    disease_indices = x0_indices + e_indices
    n_disease = len(disease_indices)

    n_x0 = len(x0_indices)
    n_e = len(e_indices)
    all_regions_indices = np.array(range(head.number_of_regions))
    healthy_indices = np.delete(all_regions_indices, disease_indices).tolist()
    n_healthy = len(healthy_indices)
    # This is an example of x0 mixed Excitability and Epileptogenicity Hypothesis:
    hyp_x0_E = DiseaseHypothesis(head.connectivity, excitability_hypothesis={tuple(x0_indices): x0_values},
                                 epileptogenicity_hypothesis={tuple(e_indices): e_values},
                                 connectivity_hypothesis={})

    LOG.info("Running hypothesis: " + hyp_x0_E.name)

    LOG.info("creating model configuration...")
    model_configuration_service = ModelConfigurationService(hyp_x0_E.get_number_of_regions())
    model_configuration = model_configuration_service.configure_model_from_hypothesis(hyp_x0_E)

    LOG.info("running LSA...")
    lsa_service = LSAService(eigen_vectors_number=None, weighted_eigenvector_sum=True)
    lsa_hypothesis = lsa_service.run_lsa(hyp_x0_E, model_configuration)

    LOG.info("running sensitivity analysis PSE LSA...")
    for m in METHODS:
        try:
            sa_results, pse_results = \
                sensitivity_analysis_pse_from_hypothesis(lsa_hypothesis, n_samples, method=m, half_range=0.1,
                                     global_coupling=[{"indices": all_regions_indices,
                                                        "bounds":
                                                                 [0.0, 2*model_configuration_service.K_unscaled[0]]}],
                                     healthy_regions_parameters= [{"name": "x0", "indices": healthy_indices}],
                                     model_configuration=model_configuration,
                                     model_configuration_service=model_configuration_service,
                                     lsa_service=lsa_service, save_services=True)

            lsa_hypothesis.plot_lsa_pse(pse_results, model_configuration,
                                        weighted_eigenvector_sum=lsa_service.weighted_eigenvector_sum,
                                        n_eig=lsa_service.eigen_vectors_number,
                                        figure_name=m + "_PSE_LSA_overview_" + lsa_hypothesis.name)
            # , show_flag=True, save_flag=False

            convert_to_h5_model(pse_results).write_to_h5(FOLDER_RES,
                                                         m + "_PSE_LSA_results_" + lsa_hypothesis.name + ".h5")
            convert_to_h5_model(sa_results).write_to_h5(FOLDER_RES,
                                                        m + "_SA_LSA_results_" + lsa_hypothesis.name + ".h5")
        except:
            warnings.warn("Method " + m + " failed!")


