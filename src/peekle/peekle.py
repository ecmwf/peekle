# (C) Copyright 2026- ECMWF and individual contributors.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.
#


"""peekle - Peek into pickle files without importing unknown dependencies.

This module provides a safe way to inspect the structure and contents of
pickle files.  Rather than importing the original classes that were pickled,
peekle synthesises stub objects that mirror the original class hierarchy,
allowing the pickle to be loaded and its structure explored without executing
arbitrary code from the original modules.

The main entry point is :class:`Peekle`, whose :meth:`Peekle.parse` class
method reads a binary file-like object and returns a :class:`PeekleObject`
tree that can be serialised to a plain Python dict/list structure via
``to_json()``.

Example::

    import json
    from peekle import Peekle

    with open("model.pkl", "rb") as f:
        result = Peekle.parse(f)

    print(json.dumps(result.to_json(), indent=2))
"""

import ast
import base64
import contextlib
import pickle
import sys
import textwrap
import types

builtin_modules = set(sys.builtin_module_names)
stdlib_modules = set(sys.stdlib_module_names)
extra_modules = {"__builtin__"}

# The complete set of module names that should be resolved normally by the
# standard unpickler rather than replaced with stubs.
BUILTIN_PACKAGES = builtin_modules | stdlib_modules | extra_modules


class PeekleObject:
    """Base class for all nodes in a peekle object tree.

    Every node produced by :class:`PeekleObjectMaker` is an instance of this
    class or one of its subclasses.  Subclasses implement ``to_json(**kwargs)``
    to serialise their value to a plain Python structure safe to pass to
    :func:`json.dumps`.
    """

    def to_python(self, **kwargs):
        """Convert to a python code representation of the deserialised instance."""

        code = self.to_code()
        try:
            from black import FileMode, format_str

            code = format_str(code, mode=FileMode())
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            print(
                f"Warning: black failed to format code, returning unformatted code. {e}",
                file=sys.stderr,
            )

        return code


class LiteralObject(PeekleObject):
    """A :class:`PeekleObject` that wraps a primitive literal value.

    Literal values are Python singletons and simple scalars: ``None``,
    ``True``, ``False``, ``Ellipsis``, integers, floats and strings.

    Args:
        value: The raw Python value being wrapped.
    """

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f"Literal({self.value})"

    def to_json(self, **kwargs):
        """Return the wrapped value, optionally shortening long strings.

        Keyword Args:
            shorten_strings (bool): When ``True``, string values longer than
                20 characters are truncated with an ellipsis placeholder.
                Defaults to ``False``.

        Returns:
            The raw Python literal, or a shortened string when
            *shorten_strings* is ``True``.
        """

        shorten_strings = kwargs.get("shorten_strings", False)

        if shorten_strings and isinstance(self.value, str):
            return textwrap.shorten(self.value, width=20, placeholder="...")

        return self.value

    def __str__(self):
        return str(self.value)

    def to_code(self):
        """Write a Python code representation of the literal value."""

        if isinstance(self.value, (type(None), bool, int, float, str, type(Ellipsis))):
            return repr(self.value)

        try:
            value = ast.parse(repr(self.value), mode="eval")
            value = ast.fix_missing_locations(value)
            value = ast.unparse(value)
        except SyntaxError:
            value = repr(repr(self.value))

        return value


class DictObject(PeekleObject):
    """A :class:`PeekleObject` that represents a :class:`dict`.

    Args:
        value (dict): A mapping from :class:`PeekleObject` keys to
            :class:`PeekleObject` values.
    """

    def __init__(self, value):
        self.value = value

    def to_json(self, **kwargs):
        """Serialise the dictionary to a plain ``dict`` with string keys.

        Each key is converted via ``str()`` so that the result is always
        JSON-serialisable.  Values are serialised recursively.

        Returns:
            dict: A plain ``{str: ...}`` mapping.
        """
        return {str(k): v.to_json(**kwargs) for k, v in self.value.items()}

    def to_code(self, **kwargs):
        """Write a Python code representation of the dictionary."""
        return (
            "{"
            + ",\n".join(
                f"{k.to_code(**kwargs)}: {v.to_code(**kwargs)}"
                for k, v in self.value.items()
            )
            + "}"
        )


class ListOject(PeekleObject):
    """A :class:`PeekleObject` that represents a :class:`list`.

    Args:
        value (list[PeekleObject]): The list elements as :class:`PeekleObject`
            instances.
    """

    def __init__(self, value):
        self.value = value

    def to_json(self, **kwargs):
        """Serialise the list elements recursively.

        Returns:
            list: A plain Python list.
        """
        return [v.to_json(**kwargs) for v in self.value]

    def to_code(self, **kwargs):
        """Write a Python code representation of the list."""
        return "[" + ",\n".join(v.to_code(**kwargs) for v in self.value) + "]"


class TupleObject(PeekleObject):
    """A :class:`PeekleObject` that represents a :class:`tuple`.

    Tuples are serialised as JSON arrays because JSON has no tuple type.

    Args:
        value (tuple[PeekleObject, ...]): The tuple elements.
    """

    def __init__(self, value):
        self.value = value

    def to_json(self, **kwargs):
        """Serialise the tuple elements as a list.

        Returns:
            list: A plain Python list representing the tuple contents.
        """
        return [v.to_json(**kwargs) for v in self.value]

    def to_code(self):
        """Write a Python code representation of the tuple."""
        return (
            "("
            + ",\n".join(v.to_code() for v in self.value)
            + ("," if len(self.value) == 1 else "")
            + ")"
        )


class SetObject(PeekleObject):
    """A :class:`PeekleObject` that represents a :class:`set`.

    Sets are serialised as JSON arrays because JSON has no set type.

    Args:
        value (set[PeekleObject]): The set elements.
    """

    def __init__(self, value):
        self.value = value

    def to_json(self, **kwargs):
        """Serialise the set elements as a list.

        Returns:
            list: A plain Python list representing the set contents.
        """
        return [v.to_json(**kwargs) for v in self.value]

    def to_code(self):
        """Write a Python code representation of the set."""
        if not self.value:
            return "set()"
        return "{" + ",\n".join(v.to_code() for v in self.value) + "}"


class TypeObject(PeekleObject):
    """A :class:`PeekleObject` that represents a class or type object.

    This is used when a :class:`type` itself (rather than an instance) is
    stored in the pickle.

    Args:
        value (type): The class object being wrapped.
    """

    def __init__(self, value):
        self.value = value

    def to_json(self, **kwargs):
        """Serialise the type.

        Keyword Args:
            function_calls (bool): When ``True`` return a fully-qualified
                dotted name string (e.g. ``"mymodule.MyClass"``).  When
                ``False`` (default) return a ``{"type": ..., "module": ...}``
                dict.

        Returns:
            str | dict: A dotted name string or a ``{"type", "module"}``
            dict, depending on *function_calls*.
        """

        function_calls = kwargs.get("function_calls", False)
        if function_calls:
            return f"{self.value.__module__}.{self.value.__name__}"

        return {"type": self.value.__name__, "module": self.value.__module__}

    def to_code(self):
        """Write a Python code representation of the type."""
        return f"{self.value.__module__}.{self.value.__name__}"


class BytesObject(PeekleObject):
    """A :class:`PeekleObject` that represents a :class:`bytes` value.

    Args:
        value (bytes): The raw bytes being wrapped.
    """

    def __init__(self, value):
        self.value = value

    def to_json(self, shorten_bytes=False, bytes_count=False, **kwargs):
        """Serialise the bytes value.

        Keyword Args:
            bytes_count (bool): When ``True`` return a human-readable string
                such as ``"bytes(1,024)"`` showing only the byte length.
                Takes precedence over *shorten_bytes*.  Defaults to ``False``.
            shorten_bytes (bool): When ``True`` return a dict whose ``"bytes"``
                key contains only the first 10 bytes decoded as UTF-8 (with
                errors ignored) followed by ``"..."``.  Defaults to ``False``.

        Returns:
            str | dict: A count string, a shortened preview dict, or a full
            ``{"bytes": <decoded str>}`` dict.
        """

        if bytes_count:
            return f"bytes({len(self.value):,})"

        if shorten_bytes:
            return {"bytes": self.value[:10].decode("utf-8", errors="ignore") + "..."}
        else:
            try:
                return {"bytes": self.value.decode("utf-8")}
            except UnicodeDecodeError:
                return {"bytes": base64.b64encode(self.value).decode("utf-8")}

    def to_code(self):
        """Write a Python code representation of the bytes."""
        return repr(self.value)


class AttributeObject(PeekleObject):
    """A :class:`PeekleObject` that represents an unknown class attribute.

    When a stub class encounters an attribute access that was not supplied via
    ``__setstate__``, an :class:`Attribute` placeholder is created.  This
    object records that placeholder in the output tree.

    Args:
        name (PeekleObject): The attribute name, wrapped as a
            :class:`PeekleObject`.
    """

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"AttributeObject({self.name})"

    def to_json(self, **kwargs):
        """Serialise as ``{"attribute": <name>}``.

        Returns:
            dict: A ``{"attribute": ...}`` dict.
        """
        return {"attribute": self.name.to_json(**kwargs)}

    def to_code(self, **kwargs):
        """Write a Python code representation of the attribute access."""
        return f"Attribute({self.name.to_code(**kwargs)})"


class ClassObject(PeekleObject):
    """A :class:`PeekleObject` representing a deserialised instance of an
    unknown class.

    Args:
        name (str): The fully-qualified class name (``"module.ClassName"``).
        members (PeekleObject): The instance's ``__dict__`` wrapped as a
            :class:`DictObject`.
    """

    def __init__(self, name, members):
        self.name = name
        self.members = members

    def to_json(self, **kwargs):
        """Serialise as ``{<name>: <members>}``.

        Returns:
            dict: A single-key dict mapping the class name to its members.
        """
        return {self.name: self.members.to_json(**kwargs)}

    def to_code(self):
        """Convert to a python code representation of the deserialised instance."""
        return (
            f"{self.name}("
            + ",\n".join(f"{k}={v.to_code()}" for k, v in self.members.value.items())
            + ")"
        )


class FunctionObject(PeekleObject):
    """A :class:`PeekleObject` representing an unknown callable that was
    invoked (via ``__reduce__``) during pickling.

    Args:
        name (str): Fully-qualified callable name.
        args (PeekleObject): Positional arguments wrapped as a
            :class:`TupleObject`.
        kwargs (PeekleObject): Keyword arguments wrapped as a
            :class:`DictObject`.
        state (PeekleObject): Optional post-construction state (from
            ``__setstate__``), or a :class:`LiteralObject` wrapping ``None``.
    """

    def __init__(self, name, args, kwargs, state):
        self.name = name
        self.args = args
        self.kwargs = kwargs
        self.state = state

    def to_json(self, **kwargs):
        """Serialise the callable invocation.

        Keyword Args:
            function_calls (bool): When ``True`` return a compact string
                representation such as ``"mymodule.MyFunc(arg1,key=val)"``.
                When ``False`` (default) return a structured dict with
                ``"name"``, ``"args"``, ``"kwargs"`` and ``"state"`` keys.

        Returns:
            str | dict: A call-string or a structured dict.
        """

        function_calls = kwargs.get("function_calls", False)

        if function_calls:
            params = [str(a) for a in self.args.to_json(**kwargs)]
            params += [f"{k}={v}" for k, v in self.kwargs.to_json(**kwargs).items()]

            if self.state.to_json(**kwargs) is not None:
                params += [f"_state={self.state.to_json(**kwargs)}"]

            return f"{self.name}({','.join(params)})"

        return {
            "name": self.name,
            "args": self.args.to_json(**kwargs),
            "kwargs": self.kwargs.to_json(**kwargs),
            "state": self.state.to_json(**kwargs),
        }

    def to_code(self, **kwargs):
        return f"{self.name}({', '.join([v.to_code() for v in self.args.value] + [f'{k}={v.to_code()}' for k, v in self.kwargs.value.items()])})"


class PersistentObject(PeekleObject):
    """A :class:`PeekleObject` representing a *persistent ID* reference.

    The pickle protocol supports persistent IDs via ``persistent_id`` /
    ``persistent_load`` hooks.  When an object was stored using such a hook,
    this wrapper records the ID so the caller can handle it.

    Args:
        id (PeekleObject): The persistent ID value, wrapped as a
            :class:`PeekleObject`.
    """

    def __init__(self, id):
        self.id = id

    def to_json(self, **kwargs):
        """Serialise as ``{"id": <id>}``.

        Returns:
            dict: A ``{"id": ...}`` dict.
        """
        return {"id": self.id.to_json(**kwargs)}

    def to_code(self, **kwargs):
        return self.id.to_code()


class UnsupportedObject(PeekleObject):
    """A :class:`PeekleObject` that wraps an object whose type is not handled
    by the current conversion logic.

    Used as a catch-all so that parsing never raises an exception for unknown
    types.

    Args:
        obj: The original Python object that could not be mapped.
    """

    def __init__(self, obj):
        self.obj = obj

    def __repr__(self):
        return f"UnsupportedObject({self.obj})"

    def to_json(self, **kwargs):
        """Serialise as ``{"unsupported": {"type": ..., "value": ...}}``.

        Returns:
            dict: A diagnostic dict containing the type name and ``repr()``
            of the unsupported object.
        """
        return {
            "unsupported": {"type": type(self.obj).__name__, "value": repr(self.obj)}
        }

    def to_code(self, **kwargs):
        """Write a Python code representation of the unsupported object."""
        return f"UnsupportedObject({repr(repr(self.obj))})"


class PeekleObjectProvider:
    """Interface for internal stub objects that can produce a
    :class:`PeekleObject`.

    All internal helper classes (:class:`ClassMixin`, :class:`FunctionMixin`,
    :class:`PersistentID`, :class:`Attribute`) implement this interface so
    that :class:`PeekleObjectMaker` can delegate to them uniformly.
    """

    def _peekle_object(self, maker):
        """Convert this internal object to a :class:`PeekleObject`.

        Args:
            maker (PeekleObjectMaker): The maker instance to use for
                recursively converting child values.

        Raises:
            NotImplementedError: Always - subclasses must override this method.
        """
        raise NotImplementedError(
            f"This method should be implemented in subclasses ({self.__class__.__name__})"
        )


class Base:
    """Dynamic base class used for synthesised stub classes.

    When :class:`PeekleUnpickler` encounters an unknown class it creates a
    dynamic subclass of ``Base`` along with two helper variants:

    * a *class* variant (mixes in :class:`ClassMixin`) - used when the object
      is unpickled without constructor arguments (typical ``__setstate__``
      path).
    * a *function* variant (mixes in :class:`FunctionMixin`) - used when the
      object's ``__reduce__`` returns positional or keyword arguments.

    ``__new__`` inspects the constructor arguments and dispatches to the
    appropriate variant so that the correct mixin is always active.
    """

    def __new__(cls, *args, **kwargs):
        """Create either a class-variant or function-variant instance.

        Returns a :class:`ClassMixin` instance when called with no arguments,
        or a :class:`FunctionMixin` instance when called with positional or
        keyword arguments.
        """

        assert hasattr(cls, "_sub_classes")

        if args or kwargs:
            return super().__new__(cls._sub_classes["function"])
        else:
            return super().__new__(cls._sub_classes["class"])


class ClassMixin(PeekleObjectProvider):
    """Mixin that gives a stub class instance-like (``__setstate__``) behaviour.

    Mixed into dynamically created stub classes when the pickled object was
    stored via ``__setstate__`` rather than constructor arguments.
    """

    def __setstate__(self, state):
        """Restore instance state from a dict (called by the unpickler).

        Args:
            state (dict): The state dict provided by the pickle stream.
        """
        self.__dict__.update(state)

    def __getattr__(self, name):
        """Return an :class:`Attribute` placeholder for unknown attributes.

        Called whenever the unpickler accesses an attribute that was not
        populated by ``__setstate__``.  Dunder names are forwarded to the
        parent implementation.

        Args:
            name (str): Attribute name.

        Returns:
            Attribute: A placeholder recording the attribute access.
        """

        if name.startswith("__"):
            return super().__getattr__(name)

        result = Attribute(name)
        setattr(self, name, result)
        return result

    @property
    def name(self):
        """Fully-qualified class name, e.g. ``"mymodule.MyClass"``."""
        return f"{self.__module__}.{self.__class__.__name__.split('/')[0]}"

    def _peekle_object(self, maker):
        """Convert this instance to a :class:`ClassObject`.

        Filters out dunder keys from ``__dict__`` and wraps the remainder.

        Args:
            maker (PeekleObjectMaker): Used to recursively wrap member values.

        Returns:
            ClassObject: A node capturing the class name and its members.
        """

        # Build the DictObject directly instead of routing the throwaway
        # comprehension dict through ``maker``.  That dict has no other
        # reference once we return, so its ``id()`` would be freed and later
        # reused by another module's transient dict, causing a false hit in
        # ``PeekleObjectMaker``'s id-keyed cache (one module inheriting
        # another's members).  The member values are real, persistent pickle
        # objects, so wrapping them through ``maker`` is still safe.
        members = DictObject(
            {
                maker(k): maker(v)
                for k, v in self.__dict__.items()
                if not k.startswith("__")
            }
        )
        return ClassObject(self.name, members)

    def __setitem__(self, key, value):
        """Store *value* under *key* in the instance's ``__dict__``."""
        self.__dict__[key] = value


class FunctionMixin(PeekleObjectProvider):
    """Mixin that gives a stub class callable-like (``__reduce__``) behaviour.

    Mixed into dynamically created stub classes when the pickled object's
    reconstruction requires passing arguments to a callable.
    """

    _args = ()
    _kwargs = {}
    _state = {}

    def __init__(self, *args, **kwargs):
        """Record constructor arguments for later inspection.

        Args:
            *args: Positional arguments passed to the callable.
            **kwargs: Keyword arguments passed to the callable.
        """
        self._args = args
        self._kwargs = kwargs
        self._state = None

    def __setstate__(self, state):
        """Record any post-construction state dict.

        Args:
            state: The state object provided by the pickle stream.
        """
        self._state = state

    def _peekle_object(self, maker):
        """Convert this instance to a :class:`FunctionObject`.

        Args:
            maker (PeekleObjectMaker): Used to recursively wrap args, kwargs
                and state.

        Returns:
            FunctionObject: A node capturing the callable name and its
            invocation details.
        """

        name = f"{self.__module__}.{self.__class__.__name__.split('/')[0]}"

        return FunctionObject(
            name,
            args=maker(self._args),
            kwargs=maker(self._kwargs),
            state=maker(self._state),
        )


class PersistentID(PeekleObjectProvider):
    """Wraps a persistent ID encountered during unpickling.

    The pickle protocol allows objects to be stored by persistent ID (a
    reference the application is expected to resolve externally).  This class
    records such an ID without resolving it.

    Args:
        id: The raw persistent ID value.
    """

    def __init__(self, id):
        self.id = id

    def _peekle_object(self, maker):
        """Convert to a :class:`PersistentObject`.

        Args:
            maker (PeekleObjectMaker): Used to wrap the raw ID value.

        Returns:
            PersistentObject: A node capturing the persistent ID.
        """

        return PersistentObject(maker(self.id))


class Attribute(PeekleObjectProvider):
    """Placeholder for an attribute accessed on a stub instance but never
    populated via ``__setstate__``.

    :class:`ClassMixin.__getattr__` creates one of these whenever the
    unpickler reads an attribute that does not exist on the synthesised class
    instance.

    Args:
        name (str): The name of the missing attribute.
    """

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"DummyAttribute({self.name})"

    def as_dict(self):
        """Return a ``{"__class__": ..., "name": ...}`` dict for debugging."""
        return {"__class__": self.__class__.__name__, "name": self.name}

    def __call__(self, *args, **kwds):
        """No-op callable - allows attribute calls to silently succeed."""
        pass
        # print(f"DummyAttribute({self.name})({args}, {kwds})")

    def _peekle_object(self, maker):
        """Convert to an :class:`AttributeObject`.

        Args:
            maker (PeekleObjectMaker): Used to wrap the attribute name.

        Returns:
            AttributeObject: A node capturing the attribute name.
        """
        return AttributeObject(maker(self.name))


class PeekleUnpickler(pickle.Unpickler):
    """Custom :class:`pickle.Unpickler` that replaces unknown classes with
    synthesised stubs.

    Standard :class:`~pickle.Unpickler` raises :class:`ModuleNotFoundError`
    or :class:`AttributeError` when it cannot import the class that was
    originally pickled.  ``PeekleUnpickler`` overrides :meth:`find_class` to
    intercept unknown modules and return dynamically-created stub classes
    instead, so the pickle stream can always be loaded without the original
    packages installed.

    Classes defined in builtins or the standard library are resolved normally
    via the parent implementation.

    Class Attributes:
        modules (dict): Cache of synthetic :class:`types.ModuleType` objects,
            keyed by module name.
        classes (dict): Cache of synthesised base classes, keyed by the
            string ``"module.ClassName"``.
    """

    modules = {}
    classes = {}

    def make_classes(self, module_name, class_name, module):
        """Create and register a trio of stub classes for an unknown type.

        For each unknown class three dynamic types are created:

        1. A *base class* - a direct subclass of :class:`Base` carrying the
           module/class name.
        2. A *class helper* - subclasses both the base class and
           :class:`ClassMixin`; used when the object is restored via
           ``__setstate__``.
        3. A *function helper* - subclasses both the base class and
           :class:`FunctionMixin`; used when the object is reconstructed via
           a callable invocation.

        :class:`Base.__new__` selects between the class and function helpers
        at instantiation time by examining constructor arguments.

        Args:
            module_name (str): Dotted module name of the unknown class.
            class_name (str): Simple class name of the unknown class.
            module (types.ModuleType): The synthetic module to attach the
                new classes to.

        Returns:
            type: The newly-created base class.
        """

        sub_classes = {}

        base_class = type(
            class_name,
            (Base,),
            {"__module__": module_name, "_sub_classes": sub_classes},
        )
        setattr(module, class_name, base_class)

        class_helper = type(
            class_name + "/peekle_class",
            (base_class, ClassMixin),
            {"__module__": module_name},
        )
        setattr(module, class_name + "/peekle_class", class_helper)

        function_helper = type(
            class_name + "/peekle_function",
            (base_class, FunctionMixin),
            {"__module__": module_name},
        )
        setattr(module, class_name + "/peekle_function", function_helper)

        sub_classes["class"] = class_helper
        sub_classes["function"] = function_helper

        return base_class

    def find_class(self, module_name, class_name):
        """Resolve a class name encountered in the pickle stream.

        Standard library and built-in classes are resolved normally via the
        parent :class:`~pickle.Unpickler`.  All other classes are handled by
        :meth:`make_classes` and cached so that the same stub is reused for
        every occurrence of the same type within a session.

        Args:
            module_name (str): The module that owns the class.
            class_name (str): The name of the class.

        Returns:
            type: The real class (for stdlib) or a synthesised stub (for
            everything else).
        """

        if module_name in BUILTIN_PACKAGES:
            return super().find_class(module_name, class_name)

        full = f"{module_name}.{class_name}"
        if full in self.classes:
            return self.classes[full]

        module = self.modules.get(module_name)
        if module is None:
            module = types.ModuleType(module_name)
            self.modules[module_name] = module

        base_class = self.make_classes(module_name, class_name, module)

        self.classes[full] = base_class

        return self.classes[full]

    def persistent_load(self, pid):
        """Wrap a persistent ID in a :class:`PersistentID` holder.

        Args:
            pid: The raw persistent ID value from the pickle stream.

        Returns:
            PersistentID: A holder that will later be converted to a
            :class:`PersistentObject` node in the output tree.
        """
        return PersistentID(pid)


class Loop(PeekleObject):
    """Sentinel :class:`PeekleObject` used when a circular reference is
    detected during object tree construction.

    :class:`PeekleObjectMaker` tracks objects currently being processed.  If
    the same object is encountered again before processing finishes, a
    ``Loop`` is returned instead of recursing infinitely.

    Args:
        maker (PeekleObjectMaker): The maker that detected the loop.
        obj: The object whose processing has not yet completed.
    """

    def __init__(self, maker, obj):
        self.maker = maker
        self.obj = obj

    def __call__(self, obj):
        """Act as a no-op maker to absorb further calls inside the cycle.

        Returns:
            Loop: Returns *self* so that downstream code receives a valid
            :class:`PeekleObject`.
        """
        return self

    def __repr__(self):
        return f"loop_{id(self.obj)}"

    def cache(self, obj, peekle_object):
        """Discard any cache attempt inside the loop and return the object as-is.

        Returns:
            PeekleObject: The *peekle_object* passed in, unchanged.
        """
        return peekle_object

    def to_json(self, **kwargs):
        """Serialise the loop marker with diagnostic information.

        Returns:
            dict: A ``{"loop": {"id": ..., "type": ..., "value": ...}}`` dict.
        """
        return {
            "loop": {
                "id": id(self.obj),
                "type": type(self.obj).__name__,
                "value": repr(self.obj),
            }
        }

    def to_code(self, **kwargs):
        """Write a Python code representation of the loop marker."""
        return f"Loop(id={id(self.obj)}, type={repr(type(self.obj).__name__)}, value={repr(repr(self.obj))})"


class PeekleObjectMaker:
    """Converts a raw Python object (as loaded by :class:`PeekleUnpickler`)
    into a tree of :class:`PeekleObject` nodes.

    Handles all standard Python container types and primitive scalars, and
    delegates to :meth:`PeekleObjectProvider._peekle_object` for stub class
    instances.  Circular references are detected via the :meth:`visit` context
    manager and represented as :class:`Loop` nodes.

    An internal cache keyed by ``id(obj)`` ensures that each distinct Python
    object is converted only once.
    """

    def __init__(self):
        self._cache = {}
        self._depth = 0
        self._busy = {}
        # Strong references to every object used as a cache key.  The cache is
        # keyed by ``id(obj)``; without pinning the keys, a transient object
        # (e.g. an inline-built container) can be freed and its address reused
        # by a later, unrelated object, producing a false cache hit.  Holding
        # the keys alive for the maker's lifetime makes ``id()`` collisions
        # impossible.
        self._keep = []

    @contextlib.contextmanager
    def visit(self, obj):
        """Context manager that tracks objects currently being processed.

        If *obj* is already being processed (i.e. is in the busy set), the
        context manager yields a :class:`Loop` sentinel instead of ``self``
        so the caller can short-circuit the conversion.

        Args:
            obj: The object about to be converted.

        Yields:
            PeekleObjectMaker | Loop: The maker itself when *obj* is not
            currently being processed, or a :class:`Loop` sentinel if a
            cycle is detected.
        """

        if id(obj) in self._busy:
            yield Loop(self, obj)
            return

        self._busy[id(obj)] = (obj, self._depth)

        self._depth += 1
        yield self
        self._depth -= 1

        del self._busy[id(obj)]

    def print(self, *args, **kwargs):
        """Debug helper that prints with indentation matching the current depth."""
        if self._depth > 0:
            print(self._depth, " " * self._depth, *args, **kwargs)
        else:
            print(self._depth, *args, **kwargs)

    def cache(self, obj, peekle_object):
        """Store a completed :class:`PeekleObject` in the cache.

        Args:
            obj: The original Python object (used as cache key via ``id``).
            peekle_object (PeekleObject): The finished wrapped representation.

        Returns:
            PeekleObject: *peekle_object* unchanged (for chaining).
        """
        self._cache[id(obj)] = peekle_object
        self._keep.append(obj)  # pin the key so its id() can't be reused
        return peekle_object

    def __call__(self, obj):
        """Convert *obj* to a :class:`PeekleObject`.

        Dispatch order:

        1. Python singletons and simple scalars → :class:`LiteralObject`.
        2. Cache hit → return the previously computed result.
        3. ``dict`` → :class:`DictObject`.
        4. ``list`` → :class:`ListOject`.
        5. ``tuple`` → :class:`TupleObject`.
        6. ``set`` → :class:`SetObject`.
        7. :class:`PeekleObjectProvider` → delegates to ``_peekle_object``.
        8. ``type`` → :class:`TypeObject`.
        9. ``bytes`` → :class:`BytesObject`.
        10. Anything else → :class:`UnsupportedObject`.

        Args:
            obj: Any Python object to be converted.

        Returns:
            PeekleObject: The wrapped representation of *obj*.
        """

        if obj in (None, True, False, Ellipsis, {}, [], tuple()):
            return LiteralObject(obj)

        if isinstance(obj, (int, float, str)):
            return LiteralObject(obj)

        if id(obj) in self._cache:
            return self._cache[id(obj)]

        with self.visit(obj) as maker:
            if isinstance(obj, dict):
                return maker.cache(
                    obj, DictObject({maker(k): maker(v) for k, v in obj.items()})
                )

            if isinstance(obj, list):
                return maker.cache(obj, ListOject([maker(i) for i in obj]))

            if isinstance(obj, tuple):
                return maker.cache(obj, TupleObject(tuple(maker(i) for i in obj)))

            if isinstance(obj, set):
                return maker.cache(obj, SetObject({maker(i) for i in obj}))

            if isinstance(obj, PeekleObjectProvider):
                return maker.cache(obj, obj._peekle_object(maker))

            if isinstance(obj, type):
                return maker.cache(obj, TypeObject(obj))

            if isinstance(obj, bytes):
                return maker.cache(obj, BytesObject(obj))

            return maker.cache(obj, UnsupportedObject(obj))


class Peekle:
    """Public entry point for inspecting pickle files.

    Parses a pickle file using a safe custom unpickler that synthesises stub
    objects for any unknown classes, then converts the result into a tree of
    :class:`PeekleObject` nodes.  The tree can be serialised to a plain Python
    dict/list structure via ``to_json()``.

    Example::

        import json
        from peekle import Peekle

        with open("model.pkl", "rb") as f:
            result = Peekle.parse(f)

        print(json.dumps(result.to_json(), indent=2))
    """

    @classmethod
    def parse(cls, file):
        """Parse a pickle file and return a :class:`PeekleObject` tree.

        The file is loaded using :class:`PeekleUnpickler`, which safely stubs
        out any unknown classes without importing the original modules.  The
        resulting Python object is then converted to a :class:`PeekleObject`
        hierarchy by :class:`PeekleObjectMaker`.

        Args:
            file: A binary file-like object open for reading (e.g. the
                result of ``open("file.pkl", "rb")``).

        Returns:
            PeekleObject: The root of the object tree representing the
            pickle's contents.
        """

        obj = PeekleUnpickler(file).load()
        maker = PeekleObjectMaker()
        result = maker(obj)
        assert isinstance(result, PeekleObject), result
        return result
