# -*- encoding: utf-8 -*-
import atexit
import enum
import os
import threading
import typing

from ddtrace.internal import forksafe
from ddtrace.internal import uwsgi
from ddtrace.vendor import attr


from .logger import get_logger


log = get_logger(__name__)


class ServiceStatus(enum.Enum):
    """A Service status."""

    STOPPED = "stopped"
    RUNNING = "running"


class ServiceAlreadyRunning(RuntimeError):
    pass


@attr.s
class Service(object):
    """A service that can be started or stopped."""

    status = attr.ib(default=ServiceStatus.STOPPED, type=ServiceStatus, init=False, eq=False)
    _service_lock = attr.ib(factory=threading.Lock, repr=False, init=False, eq=False, type=threading.Lock)

    def __enter__(self):
        # type: () -> Service
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        self.join()

    def start(self):
        # type: () -> None
        """Start the service."""
        # Use a lock so we're sure that if 2 threads try to start the service at the same time, one of them will raise
        # an error.
        with self._service_lock:
            if self.status == ServiceStatus.RUNNING:
                raise ServiceAlreadyRunning("%s is already running" % self.__class__.__name__)
            self.status = ServiceStatus.RUNNING
            self._start()

    def _start(self):
        # type: () -> None
        """Start the service for real.

        This method uses the internal lock to be sure there's no race conditions.
        """

    def stop(self):
        # type: () -> None
        """Stop the service."""
        self.status = ServiceStatus.STOPPED

    @staticmethod
    def join(
        timeout=None,  # type: float
    ):
        # type: (...) -> typing.Optional[float]
        """Join the service once stopped."""

    def copy(self):
        # type: () -> Service
        return attr.evolve(self)


class ServiceProcess(object):
    """A manager that can handle the lifecycle of a service in a process.

    This allows to handle a service that should keep running when, e.g., the Python process forks."""

    SERVICE_CLASS = None  # type: typing.ClassVar[typing.Type[Service]]

    def __init__(self, *args, **kwargs):
        # type: (...) -> None
        super(ServiceProcess, self).__init__()
        self._service = self.SERVICE_CLASS(*args, **kwargs)

    def start(self, *args, **kwargs):
        # type: (...) -> None
        """Start the service.

        :param stop_on_exit: Whether to stop the service and wait for shutdown on exit.
        :param start_in_children: Whether to start the service in child processes.
        """
        stop_on_exit = kwargs.pop("stop_on_exit", True)
        start_in_children = kwargs.pop("start_in_children", True)

        if start_in_children:
            try:
                uwsgi.check_uwsgi(self.start, atexit=self.stop if stop_on_exit else None)
            except uwsgi.uWSGIMasterProcess:
                # Do nothing, the start() method will be called in each worker subprocess
                return

        self._service.start()

        if stop_on_exit:
            atexit.register(self.stop)

        if start_in_children:
            forksafe.register(self._restart_on_fork)
            # if hasattr(os, "register_at_fork"):
            #     os.register_at_fork(after_in_child=self._restart_on_fork)
            # else:
            #     log.warning(
            #         "Your Python version does not have `os.register_at_fork`. "
            #         "You have to start a new instance of %s after fork() manually." % self.__class__.__name__
            #     )

    def stop(self, *args, **kwargs):
        # type: (...) -> None
        """Stop the service."""
        # Check for the service status: this *should* not be necessary per-say, but in the case `atexit.unregister` is
        # unavailable, we're sure not to call stop twice.
        if self._service.status == ServiceStatus.RUNNING:
            self._service.stop(*args, **kwargs)

        # Python 2 does not have unregister
        if hasattr(atexit, "unregister"):
            # You can unregister a method that was not registered, so no need to do any other check
            atexit.unregister(self.stop)

        try:
            forksafe.unregister(self._restart_on_fork)
        except ValueError:
            pass

    def __getattr__(self,
                    k, # type: typing.Any
                    ):
        # type: (...) -> typing.Any
        return getattr(self._service, k)

    def _restart_on_fork(self):
        # type: () -> None
        # Be sure to stop the parent first, since it might have to e.g. unpatch functions
        # Do not flush data as we don't want to have multiple copies of the parent profile exported.
        self.stop()
        self._service = self._service.copy()
        self.start()
