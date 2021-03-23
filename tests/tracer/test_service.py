import subprocess
import sys

from ddtrace import compat
from ddtrace.internal import service


def test_service_status():
    s = service.Service()
    assert s.status == service.ServiceStatus.STOPPED
    s.start()
    assert s.status == service.ServiceStatus.RUNNING
    s.stop()
    assert s.status == service.ServiceStatus.STOPPED
    s.start()
    assert s.status == service.ServiceStatus.RUNNING
    s.stop()
    assert s.status == service.ServiceStatus.STOPPED


def test_service_copy():
    s = service.Service()
    assert s.copy() == s.copy()
    assert s.copy() is not s.copy()
    s.start()
    s2 = s.copy()
    assert s is not s2
    assert s == s2
    assert s.status == service.ServiceStatus.RUNNING
    assert s2.status == service.ServiceStatus.STOPPED
    s.stop()
    assert s is not s2
    assert s == s2
    assert s.status == service.ServiceStatus.STOPPED
    assert s2.status == service.ServiceStatus.STOPPED


def test_service_manager():
    class MyService(service.Service):
        pass

    class MyServiceProcess(service.ServiceProcess):
        SERVICE_CLASS = MyService

    sm = MyServiceProcess()
    sm.start()
    assert sm.status == sm._service.status == service.ServiceStatus.RUNNING

    sm.stop()
    assert sm.status == sm._service.status == service.ServiceStatus.STOPPED


def call_program(*args):
    subp = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    stdout, stderr = subp.communicate()
    return stdout, stderr, subp.wait(), subp.pid


def test_service_manager_stop_on_exit(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
from ddtrace.internal import service

class MyService(service.Service):
    def stop(self):
        print("I was stopped, thank you")

class MyServiceProcess(service.ServiceProcess):
    SERVICE_CLASS = MyService

sm = MyServiceProcess()
sm.start()
""")
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0
    assert out == b"I was stopped, thank you\n"
    assert err == b""


def test_service_manager_stop_on_exit_false(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
from ddtrace.internal import service

class MyService(service.Service):
    def stop(self):
        print("I was stopped, thank you")

class MyServiceProcess(service.ServiceProcess):
    SERVICE_CLASS = MyService

sm = MyServiceProcess()
sm.start(stop_on_exit=False)
""")
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0
    assert out == b""
    assert err == b""


def test_service_manager_start_in_children_false(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
import os
import sys

from ddtrace.internal import service

class MyService(service.Service):
    def stop(self):
        super(MyService, self).stop()
        print("I was stopped, thank you")

class MyServiceProcess(service.ServiceProcess):
    SERVICE_CLASS = MyService

sm = MyServiceProcess()
sm.start(start_in_children=False)

parent_service_instance = sm._service

child_pid = os.fork()
if child_pid == 0:
    assert parent_service_instance.status == service.ServiceStatus.RUNNING
    assert sm._service == parent_service_instance
    assert sm._service is parent_service_instance
    assert sm.status == service.ServiceStatus.RUNNING
else:
    assert sm._service is parent_service_instance
    assert sm.status == service.ServiceStatus.RUNNING
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
""")
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0
    # Stopped 2 times:
    # parent stopped in child (at exit())
    # parent stopped in parent (at exit())
    assert out == b"I was stopped, thank you\nI was stopped, thank you\n"
    assert err == b""


def test_service_manager_start_in_children(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
import os
import sys

from ddtrace.internal import service

class MyService(service.Service):
    def stop(self):
        super(MyService, self).stop()
        print("I was stopped, thank you")

class MyServiceProcess(service.ServiceProcess):
    SERVICE_CLASS = MyService

sm = MyServiceProcess()
sm.start()

parent_service_instance = sm._service

child_pid = os.fork()
if child_pid == 0:
    assert parent_service_instance.status == service.ServiceStatus.STOPPED
    assert sm._service == parent_service_instance
    assert sm._service is not parent_service_instance
    assert sm.status == service.ServiceStatus.RUNNING
else:
    assert sm._service is parent_service_instance
    assert sm.status == service.ServiceStatus.RUNNING
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
""")
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0
    # Stopped 3 times:
    # parent stopped in the child (at fork())
    # child stopped in child (at exit())
    # parent stopped in parent (at exit())
    assert out == b"I was stopped, thank you\nI was stopped, thank you\nI was stopped, thank you\n"
    assert err == b""
