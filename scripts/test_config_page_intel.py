#!/usr/bin/env python3
"""Static check of the three Intel settings: IntelNeuralAllowed, IntelLowResMaxSourceHeight and
IntelLowResIntermediateScale.

Each must exist in PluginConfiguration and in its patcher mirror UpscaleSettings with one default (the
two cross the plugin/patcher boundary as one JSON string, so a name or default that differs is a silent
mismatch), have a config page control whose default and bounds match, and the engine must clamp the
scale again on read because the settings API accepts anything.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "src" / "Configuration" / "configPage.html"
CONFIG = ROOT / "src" / "Configuration" / "PluginConfiguration.cs"
SETTINGS = ROOT / "src" / "patcher" / "Transcoding" / "UpscaleSettings.cs"
ENGINE = ROOT / "src" / "patcher" / "Transcoding" / "UpscaleEngine.cs"

# name -> (C# type, C# default text, page data-default)
FIELDS = {
    "IntelNeuralAllowed": ("bool", None, "false"),
    "IntelLowResMaxSourceHeight": ("int", "480", "480"),
    "IntelLowResIntermediateScale": ("double", "1.5", "1.5"),
}


def csharp_default(source, name, ctype):
    m = re.search(r"public\s+" + ctype + r"\s+" + name + r"\s*\{\s*get;\s*set;\s*\}\s*(?:=\s*([^;]+);)?", source)
    if not m:
        return False, None
    return True, (m.group(1).strip() if m.group(1) else None)


def tag_for(page, name):
    m = re.search(r"<input\b[^>]*\bid=\"" + name + r"\"[^>]*>", page)
    return m.group(0) if m else None


def attr(tag, name):
    m = re.search(r"\b" + name + r"=\"([^\"]*)\"", tag)
    return m.group(1) if m else None


def main():
    page = PAGE.read_text(encoding="utf-8")
    config = CONFIG.read_text(encoding="utf-8")
    settings = SETTINGS.read_text(encoding="utf-8")
    engine = ENGINE.read_text(encoding="utf-8")
    failures = []

    for name, (ctype, cs_default, page_default) in FIELDS.items():
        for label, source in (("PluginConfiguration", config), ("UpscaleSettings", settings)):
            found, default = csharp_default(source, name, ctype)
            if not found:
                failures.append("%s has no %s %s property" % (label, ctype, name))
            elif default != cs_default:
                failures.append("%s.%s default is %r, expected %r" % (label, name, default, cs_default))

        tag = tag_for(page, name)
        if tag is None:
            failures.append("configPage.html has no input with id %s" % name)
            continue
        if attr(tag, "data-config") != name:
            failures.append("%s: data-config is not %s" % (name, name))
        if attr(tag, "data-default") != page_default:
            failures.append("%s: data-default is %r, expected %r" % (name, attr(tag, "data-default"), page_default))

    height = tag_for(page, "IntelLowResMaxSourceHeight")
    if height is not None:
        if attr(height, "data-kind") != "int":
            failures.append("IntelLowResMaxSourceHeight is not data-kind int")
        if attr(height, "min") != "0":
            failures.append("IntelLowResMaxSourceHeight must allow 0 (off)")
        if "required" not in height:
            failures.append("IntelLowResMaxSourceHeight is not required")

    scale = tag_for(page, "IntelLowResIntermediateScale")
    if scale is not None:
        if attr(scale, "data-kind") != "float":
            failures.append("IntelLowResIntermediateScale is not data-kind float")
        if attr(scale, "min") != "1" or attr(scale, "max") != "2":
            failures.append("IntelLowResIntermediateScale must be bounded 1 to 2")
        if "required" not in scale:
            failures.append("IntelLowResIntermediateScale is not required")

    box = tag_for(page, "IntelNeuralAllowed")
    if box is not None:
        if attr(box, "data-kind") != "bool":
            failures.append("IntelNeuralAllowed is not a bool control")
        start = page.index(box)
        help_text = page[start:start + 1500]
        if "1.4 fps" not in help_text or "0.5 fps" not in help_text:
            failures.append("IntelNeuralAllowed help text lacks the bench numbers (1.4 fps, 0.5 fps)")

    gate = tag_for(page, "NeuralAllowed")
    if gate is None or "IntelNeuralAllowed" not in (attr(gate, "data-gates") or "").split():
        failures.append("NeuralAllowed does not gate IntelNeuralAllowed")

    if not re.search(r"Math\.Max\(1\.0,\s*Math\.Min\(2\.0,", engine):
        failures.append("UpscaleEngine does not clamp the low-res intermediate scale to 1.0 to 2.0")
    if "IntelOnnxMeasured" in engine:
        failures.append("UpscaleEngine still carries the hardcoded IntelOnnxMeasured")
    if not re.search(r"Settings\?\.IntelNeuralAllowed\s*!=\s*true", engine):
        failures.append("IntelWithholdReason does not read Settings.IntelNeuralAllowed")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("ok: the three Intel settings agree across both settings classes, the page and the engine")
    return 0


if __name__ == "__main__":
    sys.exit(main())
