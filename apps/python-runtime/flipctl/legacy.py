"""Custom Slint layouts used by existing FlipCTL Python apps."""

import pathlib
import sys

import slint

from . import _libraries, key_of


def _compiler() -> "slint.native.Compiler":
    compiler = slint.native.Compiler()
    compiler.library_paths = _libraries()
    return compiler


class App:
    """A component, with its properties as attributes and its callbacks bindable.

    What the compiler hands back is a `ComponentInstance`, which speaks
    `set_property` and `set_callback` and has no attributes of its own. That is a
    workable interface and a tiresome one to write an app against, so this wraps it:
    `ui.rows = [...]` where the property exists, and a plain AttributeError naming it
    where it does not, rather than a silent assignment that draws nothing.
    """

    def __init__(self, instance, properties, callbacks):
        object.__setattr__(self, "_instance", instance)
        object.__setattr__(self, "_properties", set(properties))
        object.__setattr__(self, "_callbacks", set(callbacks))
        object.__setattr__(self, "_models", {})

    def __getattr__(self, name):
        if name in object.__getattribute__(self, "_properties"):
            return object.__getattribute__(self, "_instance").get_property(name)
        if name in object.__getattribute__(self, "_callbacks"):
            def bind(handler):
                self.on(name, handler)
                return handler
            return bind
        raise AttributeError(f"no property {name!r}; the component has {self.properties()}")

    def __setattr__(self, name, value):
        if name not in object.__getattribute__(self, "_properties"):
            raise AttributeError(f"no property {name!r}; the component has {self.properties()}")
        # An array in Slint is a model, and a plain list is not one: handing it over
        # raw is refused with \"Object is not a dict or NamedTuple\", which says
        # nothing about the list being the problem. Rows are what an app sets most,
        # so they are wrapped here rather than in every app.
        if isinstance(value, (list, tuple)):
            value = self._model(name, list(value))
        object.__getattribute__(self, "_instance").set_property(name, value)

    def _model(self, name: str, rows: list):
        """The model behind one array property, kept and refilled rather than remade.

        Slint holds the model weakly, so a fresh `ListModel` per redraw is collected
        the moment the app drops its own reference and the panel fills with \"Model
        implementation is lacking self object (in row_count)\" while the screen stops
        updating. Keeping it here fixes that, and refilling in place means a redraw
        notifies only the rows that actually changed.
        """
        models = object.__getattribute__(self, "_models")
        model = models.get(name)
        if model is None:
            model = slint.ListModel(rows)
            models[name] = model
            return model
        for i, row in enumerate(rows):
            if i >= model.row_count():
                model.append(row)
            elif model.row_data(i) != row:
                model.set_row_data(i, row)
        while model.row_count() > len(rows):
            del model[model.row_count() - 1]
        return model

    def properties(self) -> list[str]:
        return sorted(object.__getattribute__(self, "_properties"))

    def callbacks(self) -> list[str]:
        return sorted(object.__getattribute__(self, "_callbacks"))

    def on(self, name: str, handler) -> None:
        """Bind one of the component's callbacks."""
        if name not in object.__getattribute__(self, "_callbacks"):
            raise AttributeError(f"no callback {name!r}; the component has {self.callbacks()}")
        object.__getattribute__(self, "_instance").set_callback(name, handler)

    def show(self) -> None:
        object.__getattribute__(self, "_instance").show()

    def hide(self) -> None:
        object.__getattribute__(self, "_instance").hide()


def _one(result, source: str) -> App:
    """The component a file exports: the one called `App`, or its only one."""
    for diagnostic in result.diagnostics:
        print(f"flipctl: {source}: {diagnostic}", file=sys.stderr)
    names = result.component_names
    if not names:
        raise RuntimeError(f"{source}: nothing exported to draw")
    definition = result.component("App" if "App" in names else names[0])
    return App(definition.create(), definition.properties, definition.callbacks)


def load(source: str, name: str = "app.slint") -> App:
    """A component from `.slint` source, so one file can be a whole app."""
    return _one(_compiler().build_from_source(source, pathlib.Path(name)), name)


def load_file(path) -> App:
    """A component from a `.slint` file beside the script."""
    path = pathlib.Path(path)
    return _one(_compiler().build_from_path(path), str(path))


def on_key(component):
    """Bind a handler to the shell's key callback, by name rather than by character.

    `Shell` declares `callback key(string, bool)`, and a component inheriting it
    cannot re-export a callback it did not declare, so an app forwards it to one of
    its own called `keyed`. Either is bound here, whichever the component has.
    """

    def bind(handler):
        def forward(text, down):
            key = key_of(text)
            if key is not None:
                handler(key, down)

        for name in ("keyed", "key"):
            if name in component.callbacks():
                component.on(name, forward)
                return handler
        raise RuntimeError(
            "the component declares no key callback: inherit Shell and forward its "
            "`key` to a `keyed` callback of your own"
        )

    return bind


def run(component: App, main=None) -> None:
    """Show the component and run its loop until it quits.

    Slint's loop is an asyncio one, so `main` may be a coroutine and it runs beside
    the drawing on the same thread: a socket awaited there wakes the app the moment
    it is readable, which is both simpler and more accurate than a timer that looks.
    An app with nothing to wait on passes nothing and binds keys alone.
    """
    component.show()
    slint.run_event_loop(main)
