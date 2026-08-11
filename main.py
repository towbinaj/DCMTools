"""Entry point for the DICOM Toolkit.

Normal use: double-click the executable (or ``python main.py``) to open the GUI.

Service sub-commands (run an elevated console):
    DICOMToolkit.exe service install
    DICOMToolkit.exe service start | stop | remove
    DICOMToolkit.exe service run       # run receiver headless in this console
"""

import sys


def main() -> int:
    argv = sys.argv[1:]

    if argv and argv[0] == "service":
        sub = argv[1] if len(argv) > 1 else "help"
        from dcmtoolkit import service
        if sub == "_scm":
            # Launched by the Windows Service Control Manager.
            service.run_from_scm()
            return 0
        if sub == "run":
            import threading
            print("Store receiver running headless. Ctrl+C to stop.")
            stop = threading.Event()
            try:
                service._run_scp_blocking(stop.is_set)
            except KeyboardInterrupt:
                stop.set()
            return 0
        return service.handle_cli(argv[1:])

    # Default: launch the GUI.
    from dcmtoolkit.gui.app import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
