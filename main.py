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

    if argv and argv[0] == "apply-update" and len(argv) >= 3:
        # Invoked on a freshly downloaded exe to swap itself into place.
        from dcmtoolkit.updater import run_apply_update
        return run_apply_update(argv[1], int(argv[2]))

    if argv and argv[0] == "selfcheck":
        # Headless diagnostic: report optional-feature availability, then exit.
        from dcmtoolkit.gui.app import App
        from dcmtoolkit import paths
        from dcmtoolkit.tools import conformance, render
        app = App()
        app.update()
        decoders = render.decoders()
        dec_line = ", ".join(f"{k}={v}" for k, v in decoders.items())
        try:
            from dicomanonymizer import anonymize_dataset
            from pydicom.dataset import Dataset as _DS
            _t = _DS()
            _t.PatientName = "X"
            _t.PatientID = "Y"
            anonymize_dataset(_t, delete_private_tags=True)
            deid_ok = str(_t.PatientName) == ""
        except Exception:  # noqa: BLE001
            deid_ok = False
        (paths.data_dir() / "selfcheck.txt").write_text(
            f"dnd_ok={app._dnd_ok}\n"
            f"conformance_ok={conformance.available()}\n"
            f"conformance_edition={conformance.edition()}\n"
            f"render_decoders_ok={all(decoders.values()) if decoders else False}\n"
            f"render_decoders={dec_line}\n"
            f"deid_basic_profile_ok={deid_ok}\n",
            encoding="utf-8")
        app.destroy()
        return 0

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
