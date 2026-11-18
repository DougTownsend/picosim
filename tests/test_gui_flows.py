"""The Godot GUI's classroom flows (review handoff EDU-01 … EDU-05, PERF-02),
run headless against a real backend: tests/gui/edu_flows.gd drives the
real controls and prints PASS / FAIL per check.  Skipped without Godot.

Settings, the students' copies and the editor are kept away from the user's
own: HOME and PICOSIM_COPIES_DIR point into a temporary folder and
PICOSIM_EDITOR=none opens no editor."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GODOT = shutil.which('godot')


@unittest.skipUnless(GODOT, 'Godot is not installed')
class GuiFlowsTest(unittest.TestCase):
    def test_classroom_flows(self):
        tmp = tempfile.mkdtemp(prefix='picosim-gui-')
        ro = os.path.join(tmp, 'readonly')
        os.makedirs(ro)
        os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(os.chmod, ro, stat.S_IRWXU)
        env = dict(os.environ, HOME=os.path.join(tmp, 'home'), PICOSIM_COPIES_DIR=os.path.join(tmp, 'copies'),
                   PICOSIM_EDITOR='none', PYTHONPATH=ROOT,
                   PICOSIM_BUILD_DIR=os.path.join(tmp, 'build'))
        srv = subprocess.Popen([sys.executable, '-m', 'picosim.server', '--port', '0'], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(srv.stdout.close)
        self.addCleanup(srv.wait)
        self.addCleanup(srv.kill)
        port = srv.stdout.readline().strip().rsplit(':', 1)[-1]
        gui = subprocess.run([GODOT, '--headless', '--path', os.path.join(ROOT, 'gui'), '--script',
                              os.path.join(ROOT, 'tests', 'gui', 'edu_flows.gd'), '--', '--port', port, '--tmp', tmp],
                             env=env, capture_output=True, text=True, timeout=300)
        out = gui.stdout + gui.stderr
        if os.environ.get('PICOSIM_GUI_LOG'):
            print(out)
        fails = [l for l in out.splitlines() if l.startswith('FAIL')]
        self.assertFalse(fails, out)
        self.assertIn('done: 0 failing', out)
        self.assertNotIn('Cannot deselect tabs', out)
        self.assertNotIn('SCRIPT ERROR', out)


if __name__ == '__main__':
    unittest.main()
