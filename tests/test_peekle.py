# (C) Copyright 2026- ECMWF and individual contributors.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.
#


"""Tests for peekle — safe pickle inspection."""

import io
import pickle

from peekle import Peekle


class SampleModel:
    """A sample class used to exercise peekle's stub mechanism.

    When this pickle is loaded by Peekle, the unpickler cannot find the
    class in any known package (pytest assigns a non-stdlib module name),
    so it synthesises a stub and produces a ClassObject in the output tree.
    """

    def __init__(self, value: int, label: str, tags: list):
        self.value = value
        self.label = label
        self.tags = tags


def _pickle(obj) -> io.BytesIO:
    buf = io.BytesIO()
    pickle.dump(obj, buf)
    buf.seek(0)
    return buf


def test_class_object_json():
    obj = SampleModel(value=42, label="hello", tags=["a", "b", "c"])
    result = Peekle.parse(_pickle(obj))
    data = result.to_json()

    # The module name is assigned by pytest (not stdlib), so peekle stubs it.
    expected_key = f"{SampleModel.__module__}.SampleModel"

    assert data == {
        expected_key: {
            "value": 42,
            "label": "hello",
            "tags": ["a", "b", "c"],
        }
    }


class SampleFunction:
    """A stand-in for a class whose *attribute* is referenced in a pickle.

    Pickles produced by e.g. :class:`torch.autograd.Function` subclasses store
    ``getattr(SomeClass, "apply")``.  When peekle stubs ``SomeClass`` it becomes
    a synthesised type, so ``getattr`` must resolve against the stub *class*.
    """


class ReferencesClassAttribute:
    """Pickles as ``getattr(SampleFunction, "apply")``."""

    def __reduce__(self):
        return (getattr, (SampleFunction, "apply"))


def test_class_attribute_getattr():
    # Regression test: peekle used to raise
    #   AttributeError: type object 'SampleFunction' has no attribute 'apply'
    # because attribute access on a stub *class* (not instance) was unhandled.
    result = Peekle.parse(_pickle(ReferencesClassAttribute()))
    data = result.to_json()

    expected = f"{SampleFunction.__module__}.SampleFunction.apply"
    assert data == {"attribute": expected}
