# (C) Copyright 2026- ECMWF and individual contributors.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.
#


"""Tests that a pickle cannot make peekle run code of its choosing."""

import builtins
import collections
import contextlib
import importlib
import io
import os
import pickle
import subprocess
import sys

import pytest

from peekle import Peekle


class Call:
    """Pickles as ``func(*args)``; nested ``Call``s chain the results."""

    def __init__(self, func, *args):
        self.func = func
        self.args = args

    def __call__(self, *args):  # pickle wants a callable for ``func``
        raise AssertionError("never called")

    def __reduce__(self):
        return (self.func, self.args)


def _marker_shell(marker):
    return f"{sys.executable} -c \"open({str(marker)!r}, 'w')\""


def _marker_python(marker):
    return f"open({str(marker)!r}, 'w')"


def _gadgets(marker):
    """Pickles that each create *marker* if peekle runs what they name."""

    shell = _marker_shell(marker)
    python = _marker_python(marker)
    return {
        "os.system": Call(os.system, shell),
        "builtins.exec": Call(builtins.exec, python),
        "builtins.eval": Call(builtins.eval, python),
        "subprocess.check_call": Call(
            subprocess.check_call, [sys.executable, "-c", python]
        ),
        "importlib.import_module": Call(
            Call(builtins.getattr, Call(importlib.import_module, "os"), "system"), shell
        ),
    }


@pytest.mark.parametrize("protocol", [0, 2, pickle.HIGHEST_PROTOCOL])
@pytest.mark.parametrize("name", list(_gadgets("x")))
def test_gadget_does_not_run(tmp_path, name, protocol):
    marker = tmp_path / "MARKER"
    data = pickle.dumps({"payload": _gadgets(marker)[name]}, protocol=protocol)

    # Refusing the pickle is fine; running it is not.
    with contextlib.suppress(pickle.UnpicklingError, TypeError, AttributeError):
        result = Peekle.parse(io.BytesIO(data))
        result.to_json()
        result.class_names()

    assert not marker.exists(), f"{name} ran"


def test_gadget_is_recorded_not_called(tmp_path):
    marker = tmp_path / "MARKER"
    data = pickle.dumps(Call(os.system, _marker_shell(marker)), protocol=2)

    result = Peekle.parse(io.BytesIO(data)).to_json(function_calls=True)

    assert not marker.exists()
    assert result.startswith(f"{os.system.__module__}.system(")


def test_posix_system_by_name(tmp_path):
    # The global as it appears in a pickle written on Linux or macOS.
    marker = tmp_path / "MARKER"
    command = _marker_shell(marker).encode()
    data = b"cposix\nsystem\n(V" + command + b"\ntR."

    Peekle.parse(io.BytesIO(data))

    assert not marker.exists()


def test_getattr_on_real_object_is_a_placeholder():
    data = pickle.dumps(
        Call(builtins.getattr, collections.OrderedDict, "__class__"), protocol=2
    )

    assert Peekle.parse(io.BytesIO(data)).to_json() == {"attribute": "__class__"}


def test_data_types_still_load():
    obj = {
        "bytes": b"\x00\xff" * 10,
        "ordered": collections.OrderedDict(a=1),
        "default": collections.defaultdict(list, b=[2]),
        "set": {1, 2},
        "big": 2**100,
        "type": dict,
    }
    for protocol in (0, 2, pickle.HIGHEST_PROTOCOL):
        data = Peekle.parse(io.BytesIO(pickle.dumps(obj, protocol=protocol))).to_json()
        assert data["ordered"] == {"a": 1}
        assert data["default"] == {"b": [2]}
        assert sorted(data["set"]) == [1, 2]
        assert data["big"] == 2**100
        assert data["type"] == {"type": "dict", "module": "builtins"}
        assert "bytes" in data["bytes"]


def test_max_size():
    data = pickle.dumps(list(range(1000)))

    assert Peekle.parse(io.BytesIO(data), max_size=len(data)).to_json() == list(
        range(1000)
    )
    with pytest.raises(pickle.UnpicklingError):
        Peekle.parse(io.BytesIO(data), max_size=len(data) - 1)


def test_shared_references_are_walked_once():
    # 60 levels of two references to the level below: 2**60 paths.
    node = []
    for _ in range(60):
        node = [node, node]

    assert Peekle.parse(io.BytesIO(pickle.dumps(node))).class_names() == []
