"""Stand-ins for streamlit and plotly so the app code can be exercised where they are not installed.

They check the *flow* (every page runs, widgets return sensible values, charts are built from real data).
They do not check how anything looks; that needs the real libraries.
"""
import sys
import types


class FakeFigure:
    def __init__(self, *a, **k):
        self.data, self.layout, self.shapes = [], {}, []
    def add_trace(self, t): self.data.append(t); return self
    def update_layout(self, *a, **k): self.layout.update(k); return self
    def add_hline(self, *a, **k): self.shapes.append(("hline", k)); return self
    def add_vline(self, *a, **k): self.shapes.append(("vline", k)); return self
    def update_xaxes(self, *a, **k): self.layout.update({f"xaxis_{n}": v for n, v in k.items()}); return self
    def update_yaxes(self, *a, **k): self.layout.update({f"yaxis_{n}": v for n, v in k.items()}); return self


class _Trace:
    def __init__(self, **kw): self.kw = kw


def install_fake_plotly():
    go = types.ModuleType("plotly.graph_objects")
    go.Figure = FakeFigure
    go.Scatter = type("Scatter", (_Trace,), {})
    go.Bar = type("Bar", (_Trace,), {})
    go.Heatmap = type("Heatmap", (_Trace,), {})
    go.Waterfall = type("Waterfall", (_Trace,), {})
    root = types.ModuleType("plotly")
    root.graph_objects = go
    sys.modules["plotly"], sys.modules["plotly.graph_objects"] = root, go
    return go


class AttrDict(dict):
    __getattr__ = dict.get
    def __setattr__(self, k, v): self[k] = v


class _CacheData:
    cleared = 0
    def __call__(self, *a, **k):
        if len(a) == 1 and callable(a[0]) and not k:
            return a[0]
        return lambda f: f
    def clear(self): type(self).cleared += 1


class StreamlitDuplicateElementId(Exception):
    """Mirrors streamlit.errors.StreamlitDuplicateElementId."""


class FakeSt:
    """Records every call; widgets return their default so the app runs its normal first-load path."""
    def __init__(self, calls=None, state=None, clicks=(), uploads=None, ids=None):
        self.ids = ids if ids is not None else set()      # element ids seen in this script run
        self.calls = calls if calls is not None else []
        self.session_state = state if state is not None else AttrDict()
        self.cache_data = _CacheData()
        self.clicks = set(clicks)
        self.uploads = uploads or {}
    def _child(self): return FakeSt(self.calls, self.session_state, self.clicks, self.uploads, self.ids)

    def _register(self, kind, key, content):
        """Real Streamlit derives an element id from its type and parameters unless a key is given;
        two elements with the same id in one run raise StreamlitDuplicateElementId."""
        sid = (kind, "key", key) if key else (kind, "auto", content)
        if sid in self.ids:
            raise StreamlitDuplicateElementId(
                f"There are multiple `{kind}` elements with the same auto-generated ID" if not key
                else f"Duplicate key {key!r} for `{kind}`")
        self.ids.add(sid)

    def plotly_chart(self, fig, *a, **k):
        self._register("plotly_chart", k.get("key"), repr([t.kw for t in fig.data]) + repr(fig.layout))
        self.calls.append(("plotly_chart", (fig,) + a, k))

    def dataframe(self, df, *a, **k):
        self._register("dataframe", k.get("key"), df.to_string() if hasattr(df, "to_string") else repr(df))
        self.calls.append(("dataframe", (df,) + a, k))
    def __enter__(self): return self
    def __exit__(self, *a): return False
    @property
    def sidebar(self): return self._child()
    # widgets with meaningful return values
    def text_input(self, label, value="", **k): self.calls.append(("text_input", label)); return value
    def number_input(self, label, **k): self.calls.append(("number_input", label)); return k.get("value", 0)
    def checkbox(self, label, value=False, **k): self.calls.append(("checkbox", label)); return value
    def selectbox(self, label, options, index=0, **k): self.calls.append(("selectbox", label)); return list(options)[index]
    def multiselect(self, label, options, default=None, **k):
        self.calls.append(("multiselect", label)); return list(default) if default is not None else []
    def radio(self, label, options, index=0, **k): self.calls.append(("radio", label)); return list(options)[index]
    def button(self, label, **k): self.calls.append(("button", label)); return label in self.clicks
    def file_uploader(self, label, **k):
        self.calls.append(("file_uploader", label))
        return self.uploads.get(label, [] if k.get("accept_multiple_files") else None)
    def download_button(self, label, *a, **k): self.calls.append(("download_button", label)); return False
    def tabs(self, labels): self.calls.append(("tabs", tuple(labels))); return [self._child() for _ in labels]
    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [self._child() for _ in range(n)]
    def expander(self, *a, **k): return self._child()
    def spinner(self, *a, **k): return self._child()
    def container(self, *a, **k): return self._child()
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        def rec(*a, **k):
            self.calls.append((name, a, k))
            return None
        return rec
    def names(self): return [c[0] for c in self.calls]


def install_fake_streamlit(**kw):
    fake = FakeSt(**kw)
    mod = types.ModuleType("streamlit")
    for name in ("set_page_config", "title", "caption", "header", "subheader", "markdown", "metric", "dataframe", "plotly_chart",
                 "info", "warning", "error", "success", "divider", "write"):
        setattr(mod, name, getattr(fake, name))
    for name in ("text_input", "number_input", "checkbox", "selectbox", "multiselect", "radio", "button", "file_uploader", "download_button",
                 "tabs", "columns", "expander", "spinner", "container"):
        setattr(mod, name, getattr(fake, name))
    mod.sidebar, mod.session_state, mod.cache_data = fake.sidebar, fake.session_state, fake.cache_data
    # anything not listed above (st.code, st.json, ...) is recorded, like real elements, instead of failing
    mod.__getattr__ = lambda name: getattr(fake, name)
    sys.modules["streamlit"] = mod
    return fake