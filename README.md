<p align="center">
  <a href="https://github.com/ecmwf/codex/raw/refs/heads/main/Project Maturity">
    <img src="https://github.com/ecmwf/codex/raw/refs/heads/main/Project Maturity/emerging_badge.svg" alt="Maturity Level">
  </a>
  <a href="https://opensource.org/licenses/apache-2-0">
    <img src="https://img.shields.io/badge/Licence-Apache 2.0-blue.svg" alt="Licence">
  </a>
  <a href="https://github.com/ecmwf/peekle/releases">
    <img src="https://img.shields.io/github/v/release/ecmwf/peekle?color=purple&label=Release" alt="Latest Release">
  </a>
</p>

> [!IMPORTANT]
> This software is **Emerging** and subject to ECMWF's guidelines on [Software Maturity](https://github.com/ecmwf/codex/raw/refs/heads/main/Project%20Maturity).

**Peekle** peeks into pickle files without importing unknown dependencies.

You can use it to:

- See what is in a pickled file even if you do not have the code of the pickled object.
- Inspect a pickled file to find out its dependencies.

## Installation

```bash
pip install peekle
```

## Quick start

```python
import json
from peekle import Peekle

with open("model.pkl", "rb") as f:
    result = Peekle.parse(f)

print(json.dumps(result.to_json(), indent=2))
```

## Command line interface

Installing the package also provides a `peekle` command (equivalently
`python -m peekle`):

```bash
peekle model.pkl
```

```
peekle [--torch] [--json | --python | --classes]
       [--shorten-strings] [--shorten-bytes] [--bytes-count] [--function-calls]
       [--indent N] PATH
```

| Option | Description |
|--------|-------------|
| `PATH` | Path to the pickle file to inspect (required). |
| `--torch` | Treat `PATH` as a PyTorch checkpoint (a zip archive) and inspect the `data.pkl` it contains. |
| `--json` | Output the pickle contents as JSON. This is the default. |
| `--python` | Output the pickle contents as Python code instead of JSON. |
| `--classes` | List the fully-qualified class names referenced in the pickle, one per line, sorted. |
| `--shorten-strings` | Truncate strings longer than 20 characters with an ellipsis (`--json` only). |
| `--shorten-bytes` | Show only the first 10 bytes of each `bytes` value (`--json` only). |
| `--bytes-count` | Show only the length of each `bytes` value (`--json` only). |
| `--function-calls` | Render callables and types as compact call strings (`--json` only). |
| `--indent N` | Indentation level for JSON output (`--json` only, default: `2`). |

`--json`, `--python` and `--classes` are mutually exclusive. The formatting
options (`--shorten-strings`, `--shorten-bytes`, `--bytes-count`,
`--function-calls`) are only valid with `--json`.

Examples:

```bash
# Pretty-printed JSON (default)
peekle model.pkl

# Python-code representation
peekle --python model.pkl

# List every class the pickle refers to
peekle --classes model.pkl

# Compact JSON with shortened values
peekle --json --shorten-strings --bytes-count model.pkl

# Inspect the data.pkl inside a PyTorch checkpoint
peekle --torch checkpoint.pt
```

## API

### `Peekle.parse(file, max_size=None)`

Parse a binary pickle file and return the root `PeekleObject`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | binary file-like | A file opened with `open("...", "rb")` or any object with a `.read()` method |
| `max_size` | `int` or `None` | The largest pickle, in bytes, to read; a larger one raises `pickle.UnpicklingError`. Default: no limit |

**Returns** `PeekleObject` — the root of the object tree.

______________________________________________________________________

### `.to_json(**kwargs)`

Every `PeekleObject` node exposes a `to_json(**kwargs)` method that serialises
it to a plain Python value (`dict`, `list`, `str`, `int`, `float`, `bool`, or
`None`) suitable for `json.dumps()`.

The following keyword arguments are forwarded recursively to every node in the
tree:

| Keyword | Type | Default | Description |
|---------|------|---------|-------------|
| `shorten_strings` | `bool` | `False` | Truncate strings longer than 20 characters with an ellipsis |
| `shorten_bytes` | `bool` | `False` | Show only the first 10 bytes of a `bytes` value |
| `bytes_count` | `bool` | `False` | Replace `bytes` values with a human-readable count string, e.g. `"bytes(1,024)"` |
| `function_calls` | `bool` | `False` | Render callable invocations and type references as compact dotted-name strings instead of structured dicts |

______________________________________________________________________

### `.to_python(**kwargs)`

Every `PeekleObject` node exposes a `to_python()` method that returns a
`black`-style, correctly-indented **Python source code** string reconstructing
the deserialised value. This is what the CLI prints for `--python`.

```python
print(result.to_python())
```

______________________________________________________________________

### `.walk()`

Yield this node and, recursively, every descendant node in the tree. Useful for
inspecting or collecting information across the whole object graph. A node
shared by several parents is yielded once.

```python
for node in result.walk():
    ...
```

______________________________________________________________________

### `.class_name()`

Return the fully-qualified (`module.ClassName`) name that this node refers to,
or `None` for nodes that do not denote a class (literals, containers, etc.).

______________________________________________________________________

### `.class_names()`

Return the sorted, de-duplicated list of every class name referenced anywhere in
the tree (this node and all descendants). This is what the CLI prints for
`--classes`.

```python
from peekle import Peekle

with open("model.pkl", "rb") as f:
    result = Peekle.parse(f)

for name in result.class_names():
    print(name)
```

## Output format

The serialised output reflects the type of each object in the pickle:

| Pickle type | `to_json()` output |
|-------------|-------------------|
| `None`, `True`, `False`, `int`, `float`, `str` | The value itself |
| `dict` | `{"key": value, ...}` (keys are converted to strings) |
| `list` | `[value, ...]` |
| `tuple` | `[value, ...]` (JSON has no tuple type) |
| `set` | `[value, ...]` (JSON has no set type) |
| `bytes` | `{"bytes": "<decoded>"}` |
| `type` / class reference | `{"type": "ClassName", "module": "module"}` |
| Unknown class instance | `{"module.ClassName": {<members>}}` |
| Callable invocation (`__reduce__`) | `{"name": "...", "args": [...], "kwargs": {...}, "state": ...}` |
| Persistent ID | `{"id": <value>}` |
| Circular reference | `{"loop": {"id": ..., "type": "...", "value": "..."}}` |
| Unsupported type | `{"unsupported": {"type": "...", "value": "..."}}` |

## Examples

### Inspect a scikit-learn model

```python
import json
from peekle import Peekle

with open("classifier.pkl", "rb") as f:
    result = Peekle.parse(f)

# Pretty-print the full structure
print(json.dumps(result.to_json(), indent=2))
```

### Compact view with shortened values

```python
with open("classifier.pkl", "rb") as f:
    result = Peekle.parse(f)

print(json.dumps(result.to_json(shorten_strings=True, bytes_count=True), indent=2))
```

### Function-call style output

```python
with open("pipeline.pkl", "rb") as f:
    result = Peekle.parse(f)

# Renders unknown callables as "module.Class(arg1,arg2)" strings
print(json.dumps(result.to_json(function_calls=True), indent=2))
```

### Work with the object tree directly

```python
from peekle import Peekle
from peekle.peekle import ClassObject, DictObject, LiteralObject

with open("data.pkl", "rb") as f:
    result = Peekle.parse(f)

# result is a PeekleObject subclass — inspect it programmatically
if isinstance(result, ClassObject):
    print("Top-level class:", result.name)
    print("Members:", result.members.to_json())
```

## How it works

1. `Peekle.parse()` opens the pickle stream with `PeekleUnpickler`, a subclass
   of `pickle.Unpickler`.
1. Whenever the unpickler encounters a class or function,
   `PeekleUnpickler.find_class()` dynamically creates a stub class instead of
   importing the real one, whatever its module, the standard library included.
   A stub records how the pickle calls it. Stubs are cached and reused across
   calls. The exceptions are a few plain data types (`dict`, `list`, `set`,
   `frozenset`, `tuple`, `str`, `int`, `float`, `complex`, `bool`, `slice`,
   `collections.OrderedDict` and `collections.defaultdict`), which resolve to
   the real type, and `getattr` and the latin-1 `_codecs.encode` that protocol
   2 uses to store `bytes`, which get stand-ins.
1. The loaded Python object (which may contain stub instances, dicts, lists,
   etc.) is passed to `PeekleObjectMaker`, which walks the object graph and
   wraps every node in the appropriate `PeekleObject` subclass.
1. Circular references are detected during the walk and represented as `Loop`
   nodes to prevent infinite recursion.
1. Calling `to_json()` on the root node recursively serialises the entire tree.

## Support

This software is developed by ECMWF and provided as open source under the Apache 2.0 licence on a **best-effort** basis with **no formal support**.

- ECMWF does **not** provide operational support for this package.
- Bug reports and feature requests can be submitted via [GitHub Issues](https://github.com/ecmwf/peekle/issues).
- For general enquiries about ECMWF software, please contact the [ECMWF Service Desk](https://support.ecmwf.int).

Contributions are welcome — please see the [CONTRIBUTORS](CONTRIBUTORS) file and ensure any pull request includes documentation and tests.

## Licence

```
Copyright 2026- European Centre for Medium-Range Weather Forecasts (ECMWF).

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

In applying this licence, ECMWF does not waive the privileges and immunities
granted to it by virtue of its status as an intergovernmental organisation
nor does it submit to any jurisdiction.
```
