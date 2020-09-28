# Copyright 2020, OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
The integration with sklearn supports the scikit-learn compatible libraries,
it can be enabled by using ``SklearnInstrumentor``.

.. sklearn: https://github.com/scikit-learn/scikit-learn

Usage
-----

Package instrumentation example:

.. code-block:: python

    from opentelemetry.instrumentation.sklearn import SklearnInstrumentor

    # instrument the sklearn library
    SklearnInstrumentor().instrument()

    # instrument sklearn and other libraries
    SklearnInstrumentor(packages=["lightgbm", "xgboost"]).instrument()


Model intrumentation example:

.. code-block:: python

    from opentelemetry.instrumentation.sklearn import SklearnInstrumentor
    from sklearn.datasets import load_iris
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline

    X, y = load_iris(return_X_y=True)
    X_train, X_test, y_train, y_test = train_test_split(X, y)

    model = Pipeline(
        [
            ("class", RandomForestClassifier(n_estimators=10)),
        ]
    )

    model.fit(X_train, y_train)

    SklearnInstrumentor().instrument_estimator(model)

"""
import logging
import os
from functools import wraps
from importlib import import_module
from inspect import isclass
from pkgutil import iter_modules
from typing import Callable, Dict, List, MutableMapping, Sequence, Type, Union

from sklearn.base import BaseEstimator
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.tree import BaseDecisionTree

from opentelemetry.instrumentation.instrumentor import BaseInstrumentor
from opentelemetry.instrumentation.sklearn.version import __version__
from opentelemetry.trace import get_tracer

logger = logging.getLogger(__name__)


def implement_spans(
    func: Callable, estimator: Union[BaseEstimator, Type[BaseEstimator]]
):
    """Wrap the method call with a span.

    Args:
        func: A callable to be wrapped in a span
        estimator: An instance or class of an estimator.

    Returns:
        The passed function wrapped in a span.
    """
    if isclass(estimator):
        name = estimator.__name__
    else:
        name = estimator.__class__.__name__
    logger.debug("Instrumenting: %s.%s", name, func.__name__)

    @wraps(func)
    def wrapper(*args, **kwargs):
        with get_tracer(__name__, __version__).start_as_current_span(
            name="{cls}.{func}".format(cls=name, func=func.__name__)
        ):
            return func(*args, **kwargs)

    return wrapper


def get_base_estimators(packages: List[str]) -> Dict[str, Type[BaseEstimator]]:
    """Walk package hierarchies to get BaseEstimator-derived classes.

    Args:
        packages (List[str]): A list of package names to instrument.

    Returns:
        A dictionary of qualnames and classes inheriting from
        ``BaseEstimator``.
    """
    klasses = dict()
    for package_name in packages:
        lib = import_module(package_name)
        package_dir = os.path.dirname(lib.__file__)
        for (_, module_name, _) in iter_modules([package_dir]):
            # import the module and iterate through its attributes
            try:
                module = import_module(package_name + "." + module_name)
            except ImportError:
                logger.warning(
                    "Unable to import %s.%s", package_name, module_name
                )
                continue
            for attribute_name in dir(module):
                attrib = getattr(module, attribute_name)
                if isclass(attrib) and issubclass(attrib, BaseEstimator):
                    klasses[
                        ".".join([package_name, module_name, attribute_name])
                    ] = attrib
    return klasses


# Methods on which spans should be applied.
DEFAULT_METHODS = [
    "fit",
    "transform",
    "predict",
    "predict_proba",
    "_fit",
    "_transform",
    "_predict",
    "_predict_proba",
]

# Classes and their attributes which contain a list of tupled estimators
# through which we should walk recursively for estimators.
DEFAULT_NAMEDTUPLE_ATTRIBS = {
    Pipeline: ["steps"],
    FeatureUnion: ["transformer_list"],
}

# Classes and their attributes which contain an estimator or sequence of
# estimators through which we should walk recursively for estimators.
DEFAULT_ATTRIBS = {}

# Classes (including children) explicitly excluded from autoinstrumentation
DEFAULT_EXCLUDE_CLASSES = [BaseDecisionTree]

# Default packages for autoinstrumentation
DEFAULT_PACKAGES = ["sklearn"]


class SklearnInstrumentor(BaseInstrumentor):
    """Instrument a fitted sklearn model with opentelemetry spans.

    Instrument methods of ``BaseEstimator``-derived components in a sklearn
    model.  The assumption is that a machine learning model ``Pipeline`` (or
    class descendent) is being instrumented with opentelemetry. Within a
    ``Pipeline`` is some hierarchy of estimators and transformers.

    The ``instrument_estimator`` method walks this hierarchy of estimators,
    implementing each of the defined methods with its own span.

    Certain estimators in the sklearn ecosystem contain other estimators as
    instance attributes. Support for walking this embedded sub-hierarchy is
    supported with ``recurse_attribs``. This argument is a dictionary
    with classes as keys, and a list of attributes representing embedded
    estimators as values. By default, ``recurse_attribs`` is empty.

    Similar to Pipelines, there are also estimators which have class attributes
    as a list of 2-tuples; for instance, the ``FeatureUnion`` and its attribute
    ``transformer_list``. Instrumenting estimators like this is also
    supported through the ``recurse_namedtuple_attribs`` argument. This
    argument is a dictionary with classes as keys, and a list of attribute
    names representing the namedtuple list(s). By default, the
    ``recurse_namedtuple_attribs`` dictionary supports
    ``Pipeline`` with ``steps``, and ``FeatureUnion`` with
    ``transformer_list``.

    Note that spans will not be generated for any child transformer whose
    parent transformer has ``n_jobs`` parameter set to anything besides
    ``None`` or ``1``.

    Package instrumentation example:

    .. code-block:: python

        from opentelemetry.instrumentation.sklearn import SklearnInstrumentor

        # instrument the sklearn library
        SklearnInstrumentor().instrument()

        # instrument additional libraries
        packages = ["sklearn", "lightgbm", "xgboost"]
        SklearnInstrumentor(packages=packages).instrument()


    Model intrumentation example:

    .. code-block:: python

        from opentelemetry.instrumentation.sklearn import SklearnInstrumentor
        from sklearn.datasets import load_iris
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline

        X, y = load_iris(return_X_y=True)
        X_train, X_test, y_train, y_test = train_test_split(X, y)

        model = Pipeline(
            [
                ("class", RandomForestClassifier(n_estimators=10)),
            ]
        )

        model.fit(X_train, y_train)

        SklearnInstrumentor().instrument_estimator(model)

    Args:
        methods (list): A list of method names on which to instrument a span.
          This list of methods will be checked on all estimators in the model
          hierarchy. Used in package and model instrumentation
        recurse_attribs (dict): A dictionary of ``BaseEstimator``-derived
          sklearn classes as keys, with values being a list of attributes. Each
          attribute represents either an estimator or list of estimators on
          which to also implement spans. An example is
          ``RandomForestClassifier`` and its attribute ``estimators_``. Used
          in model instrumentation only.
        recurse_namedtuple_attribs (dict): A dictionary of ``BaseEstimator``-
          derived sklearn types as keys, with values being a list of
          attribute names. Each attribute represents a list of 2-tuples in
          which the first element is the estimator name, and the second
          element is the estimator. Defaults include sklearn's ``Pipeline``
          and its attribute ``steps``, and the ``FeatureUnion`` and its
          attribute ``transformer_list``. Used in model instrumentation only.
        spanner: A function with signature (func, str) which
          decorates instance methods with opentelemetry spans.
        packages: A list of additional sklearn-compatible packages to
          instrument. Used with package instrumentation only.
        exclude_classes: A list of classes to exclude from instrumentation.
          Child classes are also excluded. Default is sklearn's
          ``[BaseDecisionTree]``.
    """

    def __new__(cls, *args, **kwargs):
        """Override new.

        The base class' new method passes args and kwargs. We override because
        we init the class with configuration and Python raises TypeError when
        additional arguments are passed to the object.__new__() method.
        """
        if cls._instance is None:
            cls._instance = object.__new__(cls)

        return cls._instance

    def __init__(
        self,
        methods: List[str] = None,
        recurse_attribs: Dict[Type[BaseEstimator], List[str]] = None,
        recurse_namedtuple_attribs: Dict[
            Type[BaseEstimator], List[str]
        ] = None,
        spanner: Callable = implement_spans,
        packages: List[str] = None,
        exclude_classes: List[Type] = None,
    ):
        self.methods = methods or DEFAULT_METHODS
        self.recurse_attribs = recurse_attribs or DEFAULT_ATTRIBS
        self.recurse_namedtuple_attribs = (
            recurse_namedtuple_attribs or DEFAULT_NAMEDTUPLE_ATTRIBS
        )
        self.spanner = spanner
        self.packages = packages or DEFAULT_PACKAGES
        if exclude_classes is None:
            self.exclude_classes = tuple(DEFAULT_EXCLUDE_CLASSES)
        else:
            self.exclude_classes = tuple(exclude_classes)

    def _instrument(self, **kwargs):
        """Instrument the library, and any additional specified on init."""
        klasses = get_base_estimators(packages=self.packages)
        for _, klass in klasses.items():
            if issubclass(klass, self.exclude_classes):
                logger.debug("Not instrumenting (excluded): %s", str(klass))
            else:
                logger.debug("Instrumenting: %s", str(klass))
                for method_name in self.methods:
                    if hasattr(klass, method_name):
                        self._instrument_class_method(
                            estimator=klass, method_name=method_name
                        )

    def _uninstrument(self, **kwargs):
        """Uninstrument the library"""
        klasses = get_base_estimators(packages=self.packages)
        for _, klass in klasses.items():
            logger.debug("Uninstrumenting: %s", str(klass))
            for method_name in self.methods:
                if hasattr(klass, method_name):
                    self._uninstrument_class_method(
                        estimator=klass, method_name=method_name
                    )

    def instrument_estimator(self, estimator: BaseEstimator):
        """Instrument a fitted estimator and its hierarchy where configured.

        Args:
            estimator (BaseEstimator): A fitted ``sklearn`` estimator,
              typically a ``Pipeline`` instance.
        """
        if isinstance(estimator, self.exclude_classes):
            logger.debug(
                "Not instrumenting (excluded): %s",
                estimator.__class__.__name__,
            )
            return

        if isinstance(
            estimator, tuple(self.recurse_namedtuple_attribs.keys())
        ):
            self._instrument_estimator_namedtuple(estimator=estimator)

        if isinstance(estimator, tuple(self.recurse_attribs.keys())):
            self._instrument_estimator_attribute(estimator=estimator)

        for method_name in self.methods:
            if hasattr(estimator, method_name):
                self._instrument_instance_method(
                    estimator=estimator, method_name=method_name
                )

    def uninstrument_estimator(self, estimator: BaseEstimator):
        """Uninstrument a fitted estimator and its hierarchy where configured.

        Args:
            estimator (BaseEstimator): A fitted ``sklearn`` estimator,
              typically a ``Pipeline`` instance.
        """
        if isinstance(estimator, self.exclude_classes):
            logger.debug(
                "Not uninstrumenting (excluded): %s",
                estimator.__class__.__name__,
            )
            return

        if isinstance(
            estimator, tuple(self.recurse_namedtuple_attribs.keys())
        ):
            self._uninstrument_estimator_namedtuple(estimator=estimator)

        if isinstance(estimator, tuple(self.recurse_attribs.keys())):
            self._uninstrument_estimator_attribute(estimator=estimator)

        for method_name in self.methods:
            if hasattr(estimator, method_name):
                self._uninstrument_instance_method(
                    estimator=estimator, method_name=method_name
                )

    def _check_instrumented(
        self,
        estimator: Union[BaseEstimator, Type[BaseEstimator]],
        method_name: str,
    ) -> bool:
        """Check an estimator-method is instrumented.

        Args:
            estimator (BaseEstimator): A class or instance of an ``sklearn``
              estimator.
            method_name (str): The method name of the estimator on which to
              check for instrumentation.
        """
        orig_method_name = "_original_" + method_name
        has_original = hasattr(estimator, orig_method_name)
        orig_class, orig_method = getattr(
            estimator, orig_method_name, (None, None)
        )
        same_class = orig_class == estimator
        if has_original and same_class:
            class_method = self._unwrap_function(
                getattr(estimator, method_name)
            )
            # if they match then the subclass doesn't override
            # if they don't then the overridden method needs instrumentation
            if class_method.__name__ == orig_method.__name__:
                return True
        return False

    def _uninstrument_class_method(
        self, estimator: Type[BaseEstimator], method_name: str
    ):
        """Uninstrument a class method.

        Replaces the patched method with the original, and deletes the
        attribute which stored the original method.

        Args:
            estimator (BaseEstimator): A class or instance of an ``sklearn``
              estimator.
            method_name (str): The method name of the estimator on which to
              apply a span.
       """
        orig_method_name = "_original_" + method_name
        if isclass(estimator):
            qualname = estimator.__qualname__
        else:
            qualname = estimator.__class__.__qualname__
        if self._check_instrumented(estimator, method_name):
            logger.debug(
                "Uninstrumenting: %s.%s", qualname, method_name,
            )
            _, orig_method = getattr(estimator, orig_method_name)
            wrapper = self._function_wrapper_wrapper(orig_method)
            if wrapper is not None:
                setattr(
                    wrapper, "__wrapped__", orig_method,
                )
            else:
                setattr(
                    estimator, method_name, orig_method,
                )
            delattr(estimator, orig_method_name)
        else:
            logger.debug(
                "Already uninstrumented: %s.%s", qualname, method_name,
            )

    def _uninstrument_instance_method(
        self, estimator: BaseEstimator, method_name: str
    ):
        """Uninstrument an instance method.

        Replaces the patched method with the original, and deletes the
        attribute which stored the original method.

        Args:
            estimator (BaseEstimator): A class or instance of an ``sklearn``
              estimator.
            method_name (str): The method name of the estimator on which to
              apply a span.
       """
        orig_method_name = "_original_" + method_name
        if isclass(estimator):
            qualname = estimator.__qualname__
        else:
            qualname = estimator.__class__.__qualname__
        if self._check_instrumented(estimator, method_name):
            logger.debug(
                "Uninstrumenting: %s.%s", qualname, method_name,
            )
            _, orig_method = getattr(estimator, orig_method_name)
            setattr(
                estimator, method_name, orig_method,
            )
            delattr(estimator, orig_method_name)
        else:
            logger.debug(
                "Already uninstrumented: %s.%s", qualname, method_name,
            )

    def _instrument_class_method(
        self, estimator: Type[BaseEstimator], method_name: str
    ):
        """Instrument an estimator method with a span.

        When instrumenting we attach a tuple of (Class, method) to the
        attribute ``_original_<method_name>`` for each method. This allows
        us to replace the patched with the original in unstrumentation, but
        also allows proper instrumentation of child classes without
        instrumenting inherited methods twice.

        Args:
            estimator (BaseEstimator): A ``BaseEstimator``-derived
              class
            method_name (str): The method name of the estimator on which to
              apply a span.
        """
        if self._check_instrumented(estimator, method_name):
            logger.debug(
                "Already instrumented: %s.%s",
                estimator.__qualname__,
                method_name,
            )
            return
        class_attr = getattr(estimator, method_name)
        if isinstance(class_attr, property):
            logger.debug(
                "Not instrumenting found property: %s.%s",
                estimator.__qualname__,
                method_name,
            )
        else:
            wrapper = self._function_wrapper(class_attr)
            if wrapper is not None:
                setattr(
                    estimator,
                    "_original_" + method_name,
                    (estimator, wrapper.__wrapped__),
                )
                setattr(
                    wrapper,
                    "__wrapped__",
                    self.spanner(wrapper.__wrapped__, estimator),
                )
            else:
                setattr(
                    estimator,
                    "_original_" + method_name,
                    (estimator, class_attr),
                )
                setattr(
                    estimator,
                    method_name,
                    self.spanner(class_attr, estimator),
                )

    def _function_wrapper(self, function):
        """Get the inner-most decorator of a function."""
        if hasattr(function, "__wrapped__"):
            if hasattr(function.__wrapped__, "__wrapped__"):
                return self._function_wrapper(function.__wrapped__)
            return function
        return None

    def _function_wrapper_wrapper(self, function):
        """Get the second inner-most decorator of a function"""
        if hasattr(function, "__wrapped__"):
            if hasattr(function.__wrapped__, "__wrapped__"):
                if hasattr(function.__wrapped__.__wrapped__, "__wrapped__"):
                    return self._function_wrapper_wrapper(function.__wrapped__)
                return function
        return None

    def _unwrap_function(self, function):
        """Fetch the function underlying any decorators"""
        if hasattr(function, "__wrapped__"):
            return self._unwrap_function(function.__wrapped__)
        return function

    def _instrument_instance_method(
        self, estimator: BaseEstimator, method_name: str
    ):
        """Instrument an estimator instance method with a span.

        When instrumenting we attach a tuple of (Class, method) to the
        attribute ``_original_<method_name>`` for each method. This allows
        us to replace the patched with the original in unstrumentation.

        Args:
            estimator (BaseEstimator): A fitted ``sklearn`` estimator.
            method_name (str): The method name of the estimator on which to
              apply a span.
        """
        if self._check_instrumented(estimator, method_name):
            logger.debug(
                "Already instrumented: %s.%s",
                estimator.__class__.__qualname__,
                method_name,
            )
            return

        class_attr = getattr(type(estimator), method_name, None)
        if isinstance(class_attr, property):
            logger.debug(
                "Not instrumenting found property: %s.%s",
                estimator.__class__.__qualname__,
                method_name,
            )
        else:
            method = getattr(estimator, method_name)
            setattr(estimator, "_original_" + method_name, (estimator, method))
            setattr(
                estimator, method_name, self.spanner(method, estimator),
            )

    def _instrument_estimator_attribute(self, estimator: BaseEstimator):
        """Instrument instance attributes which also contain estimators.

        Handle instance attributes which are also estimators, are a list
        (Sequence) of estimators, or are mappings (dictionary) in which
        the values are estimators.

        Examples include ``RandomForestClassifier`` and
        ``MultiOutputRegressor`` instances which have attributes
        ``estimators_`` attributes.

        Args:
            estimator (BaseEstimator): A fitted ``sklearn`` estimator, with an
              attribute which also contains an estimator or collection of
              estimators.
        """
        attribs = self.recurse_attribs.get(estimator.__class__, [])
        for attrib in attribs:
            attrib_value = getattr(estimator, attrib)
            if isinstance(attrib_value, Sequence):
                for value in attrib_value:
                    self.instrument_estimator(estimator=value)
            elif isinstance(attrib_value, MutableMapping):
                for value in attrib_value.values():
                    self.instrument_estimator(estimator=value)
            else:
                self.instrument_estimator(estimator=attrib_value)

    def _instrument_estimator_namedtuple(self, estimator: BaseEstimator):
        """Instrument attributes with (name, estimator) tupled components.

        Examples include Pipeline and FeatureUnion instances which
        have attributes steps and transformer_list, respectively.

        Args:
            estimator: A fitted sklearn estimator, with an attribute which also
              contains an estimator or collection of estimators.
        """
        attribs = self.recurse_namedtuple_attribs.get(estimator.__class__, [])
        for attrib in attribs:
            for _, est in getattr(estimator, attrib):
                self.instrument_estimator(estimator=est)

    def _uninstrument_estimator_attribute(self, estimator: BaseEstimator):
        """Uninstrument instance attributes which also contain estimators.

        Handle instance attributes which are also estimators, are a list
        (Sequence) of estimators, or are mappings (dictionary) in which
        the values are estimators.

        Examples include ``RandomForestClassifier`` and
        ``MultiOutputRegressor`` instances which have attributes
        ``estimators_`` attributes.

        Args:
            estimator (BaseEstimator): A fitted ``sklearn`` estimator, with an
              attribute which also contains an estimator or collection of
              estimators.
        """
        attribs = self.recurse_attribs.get(estimator.__class__, [])
        for attrib in attribs:
            attrib_value = getattr(estimator, attrib)
            if isinstance(attrib_value, Sequence):
                for value in attrib_value:
                    self.uninstrument_estimator(estimator=value)
            elif isinstance(attrib_value, MutableMapping):
                for value in attrib_value.values():
                    self.uninstrument_estimator(estimator=value)
            else:
                self.uninstrument_estimator(estimator=attrib_value)

    def _uninstrument_estimator_namedtuple(self, estimator: BaseEstimator):
        """Uninstrument attributes with (name, estimator) tupled components.

        Examples include Pipeline and FeatureUnion instances which
        have attributes steps and transformer_list, respectively.

        Args:
            estimator: A fitted sklearn estimator, with an attribute which also
              contains an estimator or collection of estimators.
        """
        attribs = self.recurse_namedtuple_attribs.get(estimator.__class__, [])
        for attrib in attribs:
            for _, est in getattr(estimator, attrib):
                self.uninstrument_estimator(estimator=est)
