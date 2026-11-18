"""Small helper for building shader node trees from Python.

`NodeBuilder` wraps a node tree and lets every helper accept either a socket
or a plain constant, so graph code reads like math instead of wiring.
"""

import bpy

NodeSocket = bpy.types.NodeSocket


def _is_socket(x):
    return isinstance(x, NodeSocket)


class NodeBuilder:
    def __init__(self, tree):
        self.tree = tree
        self.nodes = tree.nodes
        self.links = tree.links
        # Sections: every node made while a section is set is put in a frame of
        # that name by layout() (materials only; groups don't use them).
        self.current = None
        self._section = {}              # node pointer -> section (names change when renamed)
        self._order = []                # sections in the order they were first used

    # ── sections ─────────────────────────────────────────────────────────────
    def section(self, name):
        """Put the nodes made from now on in the frame `name`. Returns the previous
        section so a helper can switch back."""
        prev = self.current
        self.current = name
        if name is not None and name not in self._order:
            self._order.append(name)
        return prev

    # ── basics ───────────────────────────────────────────────────────────────
    def node(self, kind, label=None, **props):
        n = self.nodes.new(kind)
        if self.current is not None:
            self._section[n.as_pointer()] = self.current
        if label:
            n.label = label
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def feed(self, socket, value):
        """Link a socket or set a constant on an input socket."""
        if value is None:
            return
        if _is_socket(value):
            self.links.new(value, socket)
            return
        try:
            n = len(socket.default_value)
        except TypeError:
            socket.default_value = value
            return
        if isinstance(value, (int, float)):
            value = (value,) * n
            if n == 4:
                value = value[:3] + (1.0,)
        value = tuple(value)
        if len(value) < n:
            value = value + (1.0,) * (n - len(value))
        socket.default_value = value[:n]

    @staticmethod
    def inp(node, identifier):
        for s in node.inputs:
            if s.identifier == identifier:
                return s
        return node.inputs[identifier]

    @staticmethod
    def out(node, identifier):
        for s in node.outputs:
            if s.identifier == identifier:
                return s
        return node.outputs[identifier]

    # ── math ─────────────────────────────────────────────────────────────────
    def math(self, op, a, b=None, c=None, clamp=False, label=None):
        n = self.node("ShaderNodeMath", label, operation=op, use_clamp=clamp)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        if c is not None:
            self.feed(n.inputs[2], c)
        return n.outputs[0]

    def add(self, a, b, clamp=False):
        return self.math("ADD", a, b, clamp=clamp)

    def sub(self, a, b, clamp=False):
        return self.math("SUBTRACT", a, b, clamp=clamp)

    def mul(self, a, b, clamp=False):
        return self.math("MULTIPLY", a, b, clamp=clamp)

    def clamp01(self, a):
        return self.math("ADD", a, 0.0, clamp=True)

    def lerp(self, a, b, t, clamp=False):
        """Float lerp a→b by t."""
        n = self.node("ShaderNodeMix", data_type="FLOAT", clamp_factor=clamp)
        self.feed(self.inp(n, "Factor_Float"), t)
        self.feed(self.inp(n, "A_Float"), a)
        self.feed(self.inp(n, "B_Float"), b)
        return self.out(n, "Result_Float")

    def mix(self, a, b, t, blend="MIX", label=None, clamp_result=False):
        """Colour mix a→b by t with the given blend mode."""
        n = self.node("ShaderNodeMix", label, data_type="RGBA", blend_type=blend,
                      clamp_factor=True, clamp_result=clamp_result)
        self.feed(self.inp(n, "Factor_Float"), t)
        self.feed(self.inp(n, "A_Color"), a)
        self.feed(self.inp(n, "B_Color"), b)
        return self.out(n, "Result_Color")

    def map_range(self, v, from_min, from_max, to_min=0.0, to_max=1.0,
                  mode="LINEAR", clamp=True, label=None):
        n = self.node("ShaderNodeMapRange", label, interpolation_type=mode, clamp=clamp)
        self.feed(n.inputs["Value"], v)
        self.feed(n.inputs["From Min"], from_min)
        self.feed(n.inputs["From Max"], from_max)
        self.feed(n.inputs["To Min"], to_min)
        self.feed(n.inputs["To Max"], to_max)
        return n.outputs["Result"]

    def separate(self, color, label=None):
        n = self.node("ShaderNodeSeparateColor", label)
        self.feed(n.inputs[0], color)
        return n.outputs  # Red, Green, Blue

    def combine(self, r, g, b, label=None):
        n = self.node("ShaderNodeCombineColor", label)
        self.feed(n.inputs[0], r)
        self.feed(n.inputs[1], g)
        self.feed(n.inputs[2], b)
        return n.outputs[0]

    def vmath(self, op, a, b=None, scale=None, label=None, c=None):
        n = self.node("ShaderNodeVectorMath", label, operation=op)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        if c is not None:
            self.feed(n.inputs[2], c)
        if scale is not None:
            self.feed(n.inputs["Scale"], scale)
        return n.outputs[1] if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else n.outputs[0]

    def value(self, v, label=None):
        n = self.node("ShaderNodeValue", label)
        n.outputs[0].default_value = float(v)
        return n.outputs[0]

    def rgb(self, c, label=None):
        n = self.node("ShaderNodeRGB", label)
        c = tuple(c) + (1.0,) * (4 - len(c))
        n.outputs[0].default_value = c[:4]
        return n.outputs[0]

    def group(self, group_tree, label=None):
        n = self.node("ShaderNodeGroup", label)
        n.node_tree = group_tree
        return n

    # ── layout ───────────────────────────────────────────────────────────────
    def layout(self, x_gap=260, y_gap=40, band_gap=120):
        """Arrange nodes in columns by their depth from the output. With sections,
        each section is a horizontal band (same columns) inside its own frame."""
        nodes = [n for n in self.nodes if n.type != "FRAME"]
        parents = {n: [] for n in nodes}   # node -> nodes it feeds
        for l in self.links:
            if l.from_node in parents and l.to_node in parents:
                parents[l.from_node].append(l.to_node)

        depth = {}

        def d(n, stack=()):
            if n in depth:
                return depth[n]
            if n in stack or not parents[n]:
                depth[n] = 0
                return 0
            depth[n] = 1 + max(d(p, stack + (n,)) for p in parents[n])
            return depth[n]

        for n in nodes:
            d(n)
        if not self._section:
            self._place(nodes, depth, 0.0, x_gap, y_gap)
            return
        bands = {}
        for n in nodes:
            bands.setdefault(self._section.get(n.as_pointer(), "Material"), []).append(n)
        order = [s for s in self._order if s in bands] + [s for s in bands if s not in self._order]
        order.sort(key=lambda s: FIRST.index(s) if s in FIRST else len(FIRST))
        y = 0.0
        for name in order:
            ns = bands[name]
            bottom = self._place(ns, depth, y, x_gap, y_gap)
            frame = self.nodes.new("NodeFrame")
            frame.label = name
            frame.name = "FT " + name
            frame.label_size = 24
            col = FRAME_COLORS.get(name)
            if col is not None:
                frame.use_custom_color = True
                frame.color = col
            for n in ns:
                loc = n.location.copy()
                n.parent = frame
                if hasattr(n, "location_absolute"):
                    n.location_absolute = loc
                else:
                    n.location = loc
            y = bottom - band_gap

    def _place(self, nodes, depth, top, x_gap, y_gap):
        """Columns by depth starting at y = top; returns the lowest y used."""
        cols = {}
        for n in nodes:
            cols.setdefault(depth[n], []).append(n)
        low = top
        for col, ns in cols.items():
            # Keep creation order inside a column: it roughly follows data flow.
            y = top
            for n in ns:
                n.location = (-col * x_gap, y)
                h = n.dimensions.y if n.dimensions.y > 0 else _estimate_height(n)
                y -= h + y_gap
            low = min(low, y)
        return low


# Bands shown first (top), in this order; the rest follow in the order the builder made them
FIRST = ("Adjust (tweak here)", "Parameters (game values)")
# Frame colours for the sections every material has (others stay grey)
FRAME_COLORS = {
    "Adjust (tweak here)": (0.20, 0.36, 0.22),
    "Parameters (game values)": (0.20, 0.28, 0.42),
}


def _estimate_height(n):
    rows = sum(1 for s in n.inputs if s.enabled and not s.hide) + \
        sum(1 for s in n.outputs if s.enabled and not s.hide)
    extra = 260 if n.type == "TEX_IMAGE" else 0
    return 40 + rows * 22 + extra


# ── node-group interface helpers ─────────────────────────────────────────────
SOCKET_TYPES = {
    "FLOAT": "NodeSocketFloat",
    "COLOR": "NodeSocketColor",
    "VECTOR": "NodeSocketVector",
    "SHADER": "NodeSocketShader",
}


def new_group(name):
    """Create (or empty and reuse) a shader node group."""
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, "ShaderNodeTree")
    else:
        ng.nodes.clear()
        ng.interface.clear()
    return ng


def add_socket(ng, name, kind, in_out="INPUT", default=None, lo=None, hi=None, panel=None, desc=""):
    kw = {"parent": panel} if panel is not None else {}
    s = ng.interface.new_socket(name, description=desc, in_out=in_out, socket_type=SOCKET_TYPES[kind], **kw)
    if default is not None and hasattr(s, "default_value"):
        if kind == "COLOR":
            default = tuple(default) + (1.0,) * (4 - len(default))
        s.default_value = default
    if lo is not None and hasattr(s, "min_value"):
        s.min_value = lo
    if hi is not None and hasattr(s, "max_value"):
        s.max_value = hi
    return s
