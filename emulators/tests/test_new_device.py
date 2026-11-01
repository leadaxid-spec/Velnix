import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import new_device as nd  # noqa: E402


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBuildSource(unittest.TestCase):
    def test_three_groups_in_skeleton(self):
        code = nd.build_source("valve", "Клапан", [dict(s) for s in nd._EXAMPLE])
        self.assertIn('class ValveDevice(Device)', code)
        self.assertIn("свойство", code)  # в докстринге подсказка про группы
        for kw in ("tick", "on_command", "add_signal"):
            self.assertIn(kw, code)

    def test_ident_validation(self):
        for bad in ("Valve", "my-valve", "2x", "class"):
            with self.assertRaises(SystemExit):
                nd._ident(bad)
        self.assertEqual(nd._ident("my_valve2"), "my_valve2")


class TestScaffold(unittest.TestCase):
    def test_scaffold_compiles_and_groups(self):
        tmp = tempfile.mkdtemp(prefix="nd_")
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            path, code = nd.scaffold("valve", "Клапан", dest_dir=tmp)
            compile(code, path, "exec")
            self.assertIn("emul_valve", code)
            self.assertIn("Порог", code)  # setting
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_overwrite(self):
        tmp = tempfile.mkdtemp(prefix="nd_")
        try:
            nd.scaffold("valve", "Клапан", dest_dir=tmp)
            with self.assertRaises(SystemExit):
                nd.scaffold("valve", "Другое", dest_dir=tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestClone(unittest.TestCase):
    def test_clone_copies_signals(self):
        sigs = nd._clone_signals("light")
        names = {s["name"] for s in sigs}
        self.assertIn("lamp", names)
        self.assertNotIn("fw", names)  # обвязку не тащим


if __name__ == "__main__":
    unittest.main()
