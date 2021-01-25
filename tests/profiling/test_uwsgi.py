# -*- encoding: utf-8 -*-
import os
import subprocess
import tempfile
import signal
import time
import re
import sys

import pytest

uwsgi_app = os.path.join(os.path.dirname(__file__), "uwsgi-app.py")


@pytest.fixture
def uwsgi():
    # Do not use pytest tmpdir fixtures which generate directories longer than allowed for a socket file name
    socket_name = tempfile.mktemp()
    cmd = ["uwsgi", "--need-app", "--die-on-term", "--socket", socket_name, "--wsgi-file", uwsgi_app]

    def _run_uwsgi(*args):
        env = os.environ.copy()
        if sys.version_info[0] == 2:
            # On Python 2, it's impossible to import uwsgidecorators without this hack
            env["PYTHONPATH"] = os.path.join(
                os.environ.get("VIRTUAL_ENV", ""),
                "lib",
                "python%s.%s" % (sys.version_info[0], sys.version_info[1]),
                "site-packages",
            )

        return subprocess.Popen(cmd + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)

    try:
        yield _run_uwsgi
    finally:
        os.unlink(socket_name)


def test_uwsgi_threads_disabled(uwsgi):
    proc = uwsgi()
    stdout, _ = proc.communicate()
    assert proc.wait() != 0
    assert b"ddtrace.internal.uwsgi.uWSGIConfigError: enable-threads option must be set to true" in stdout


def test_uwsgi_threads_enabled(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads")
    worker_pids = _get_worker_pids(proc.stdout, 1)
    # Give some time to the process to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 30
    for pid in worker_pids:
        assert os.path.exists("%s.%d.1" % (filename, pid))


def test_uwsgi_threads_processes_no_master(uwsgi, monkeypatch):
    proc = uwsgi("--enable-threads", "--processes", "2")
    stdout, _ = proc.communicate()
    assert (
        b"ddtrace.internal.uwsgi.uWSGIConfigError: master option must be enabled when multiple processes are used"
        in stdout
    )


def _get_worker_pids(stdout, num_worker, num_app_started=1):
    worker_pids = []
    started = 0
    while True:
        line = stdout.readline()
        if line == b"":
            break
        elif b"WSGI app 0 (mountpoint='') ready" in line:
            started += 1
        else:
            m = re.match(r"^spawned uWSGI worker \d+ .*\(pid: (\d+),", line.decode())
            if m:
                worker_pids.append(int(m.group(1)))

        if len(worker_pids) == num_worker and num_app_started == started:
            break

    return worker_pids


def test_uwsgi_threads_processes_master(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--master", "--processes", "2")
    worker_pids = _get_worker_pids(proc.stdout, 2, 1)
    # Give some time to child to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 0
    for pid in worker_pids:
        assert os.path.exists("%s.%d.1" % (filename, pid))


def test_uwsgi_threads_processes_master_lazy_apps(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--master", "--processes", "2", "--lazy-apps")
    worker_pids = _get_worker_pids(proc.stdout, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 0
    for pid in worker_pids:
        assert os.path.exists("%s.%d.1" % (filename, pid))


def test_uwsgi_threads_processes_no_master_lazy_apps(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--processes", "2", "--lazy-apps")
    worker_pids = _get_worker_pids(proc.stdout, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    # The processes are started without a master/parent so killing one does not kill the other:
    # Kill them all and wait until they die.
    for pid in worker_pids:
        os.kill(pid, signal.SIGTERM)
    # The first worker is our child, we can wait for it "normally"
    os.waitpid(worker_pids[0], 0)
    # The other ones are grandchildren, we can't wait for it with `waitpid`
    for pid in worker_pids[1:]:
        # Wait for the uwsgi workers to all die
        while True:
            try:
                os.kill(pid, 0)
            except OSError:
                break
    for pid in worker_pids:
        assert os.path.exists("%s.%d.1" % (filename, pid))
