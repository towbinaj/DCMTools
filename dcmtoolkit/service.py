"""Windows service wrapper for the Store Receiver.

The same receiver that runs inside the GUI can also run headless as an
always-on Windows service (like the legacy DCMStoreService). It reads the
shared ``receiver.json`` config from the data directory.

Command-line (run elevated):

    DICOMToolkit.exe service install     # register + set to auto-start
    DICOMToolkit.exe service start
    DICOMToolkit.exe service stop
    DICOMToolkit.exe service remove

When the Windows Service Control Manager launches the program, control is
handed to :func:`run_from_scm`.
"""

from __future__ import annotations

import sys
import time

SERVICE_NAME = "DICOMToolkitReceiver"
SERVICE_DISPLAY = "DICOM Toolkit Store Receiver"
SERVICE_DESCRIPTION = ("DICOM C-STORE SCP receiver (modern replacement for "
                       "DCMStoreService).")


def _run_scp_blocking(stop_check):
    """Start the SCP and block until ``stop_check()`` returns True."""
    from . import config
    from .logging_setup import setup
    from .net.scp import StoreSCP

    setup()
    cfg = config.load_receiver_config()
    scp = StoreSCP(cfg)
    scp.start()
    try:
        while not stop_check():
            time.sleep(1.0)
    finally:
        scp.stop()


try:
    import win32event
    import win32service
    import win32serviceutil
    import servicemanager

    class DCMReceiverService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY
        _svc_description_ = SERVICE_DESCRIPTION

        def __init__(self, args):
            super().__init__(args)
            self._stop_evt = win32event.CreateEvent(None, 0, 0, None)
            self._stopping = False

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stopping = True
            win32event.SetEvent(self._stop_evt)

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""))
            _run_scp_blocking(lambda: self._stopping)

    _HAS_WIN32 = True
except Exception:  # noqa: BLE001 - pywin32 unavailable (non-Windows/dev)
    DCMReceiverService = None
    _HAS_WIN32 = False


def run_from_scm() -> bool:
    """If launched by the Service Control Manager, start dispatch. Returns True
    if we handled SCM startup."""
    if not _HAS_WIN32:
        return False
    try:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(DCMReceiverService)
        servicemanager.StartServiceCtrlDispatcher()
        return True
    except Exception:  # noqa: BLE001 - not started by SCM
        return False


def handle_cli(argv: list[str]) -> int:
    """Handle ``service <install|start|stop|remove>`` sub-commands."""
    if not _HAS_WIN32:
        print("Windows service support requires pywin32 on Windows.")
        return 2

    # win32serviceutil expects: <prog> <command>
    cmd = argv[0] if argv else "help"
    if cmd == "install":
        win32serviceutil.InstallService(
            None, SERVICE_NAME, SERVICE_DISPLAY,
            description=SERVICE_DESCRIPTION,
            startType=win32service.SERVICE_AUTO_START,
            exeName=_service_exe(), exeArgs="service _scm")
        print(f"Installed service '{SERVICE_DISPLAY}'.")
        return 0
    if cmd in ("start", "stop", "remove"):
        try:
            if cmd == "start":
                win32serviceutil.StartService(SERVICE_NAME)
            elif cmd == "stop":
                win32serviceutil.StopService(SERVICE_NAME)
            else:
                win32serviceutil.RemoveService(SERVICE_NAME)
            print(f"Service {cmd} OK.")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"Service {cmd} failed: {exc}")
            return 1
    print(__doc__)
    return 0


def _service_exe() -> str:
    """The executable Windows should run for the service."""
    import os
    if getattr(sys, "frozen", False):
        return sys.executable
    # dev mode: run pythonw with -m dcmtoolkit.service_main
    return win32serviceutil.LocatePythonServiceExe() or sys.executable
