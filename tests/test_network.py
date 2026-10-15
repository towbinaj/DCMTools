"""Network round-trips against throwaway localhost SCPs."""

from __future__ import annotations

from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import (Verification, ModalityWorklistInformationFind,
                                  ModalityPerformedProcedureStep)

from dcmtoolkit.model import Node
from dcmtoolkit.net import scu, mwl, mpps
from conftest import make_ct, save, storage_scp


def test_c_echo_success():
    ae = AE(ae_title="ECHOSCP")
    ae.add_supported_context(Verification)
    server = ae.start_server(("127.0.0.1", 0), block=False,
                             evt_handlers=[(evt.EVT_C_ECHO, lambda e: 0x0000)])
    try:
        node = Node(name="e", aetitle="ECHOSCP", host="127.0.0.1",
                    port=server.server_address[1], timeout=10)
        assert scu.c_echo("ME", node, timeout=10).success
    finally:
        server.shutdown()


def test_c_echo_calling_ae_override():
    seen = {}
    def h(e):
        seen["ae"] = str(e.assoc.requestor.ae_title).strip()
        return 0x0000
    ae = AE(ae_title="ECHOSCP")
    ae.add_supported_context(Verification)
    server = ae.start_server(("127.0.0.1", 0), block=False,
                             evt_handlers=[(evt.EVT_C_ECHO, h)])
    try:
        node = Node(name="e", aetitle="ECHOSCP", host="127.0.0.1",
                    port=server.server_address[1], calling_aetitle="OVERRIDE")
        scu.c_echo("GLOBAL", node, timeout=10)
        assert seen["ae"] == "OVERRIDE"
    finally:
        server.shutdown()


def test_c_store_roundtrip(tmp_path):
    received = []
    def h(e):
        received.append(str(e.dataset.SOPInstanceUID))
        return 0x0000
    files = [save(make_ct(), tmp_path / f"{i}.dcm") for i in range(3)]
    with storage_scp(handler=h) as node:
        res = scu.c_store("ME", node, files, workers=2)
    assert res.sent == 3 and res.failed == 0
    assert len(received) == 3


def test_worklist_query():
    item = Dataset()
    item.PatientID = "MRN1"
    item.PatientName = "DOE^JANE"
    item.AccessionNumber = "ACC1"
    item.StudyInstanceUID = "1.2.826.0.1.3680043.8.498.1"
    sps = Dataset()
    sps.Modality = "CT"
    sps.ScheduledStationAETitle = "CTSTATION"
    sps.ScheduledProcedureStepStartDate = "20260101"
    item.ScheduledProcedureStepSequence = [sps]

    def h(event):
        yield 0xFF00, item
    ae = AE(ae_title="MWLSCP")
    ae.add_supported_context(ModalityWorklistInformationFind)
    server = ae.start_server(("127.0.0.1", 0), block=False,
                             evt_handlers=[(evt.EVT_C_FIND, h)])
    try:
        node = Node(name="ris", aetitle="MWLSCP", host="127.0.0.1",
                    port=server.server_address[1], timeout=10)
        ident = mwl.build_worklist_query(sps_station_ae="CTSTATION")
        res = mwl.query_worklist("MOD", node, ident, timeout=10)
        assert len(res.datasets) == 1
        flat = mwl.flatten_item(res.datasets[0])
        assert flat["PatientID"] == "MRN1" and flat["Modality"] == "CT"
    finally:
        server.shutdown()


def test_mpps_create_and_set():
    store = {}
    def h_create(e):
        store[e.request.AffectedSOPInstanceUID] = e.attribute_list
        return 0x0000, e.attribute_list
    def h_set(e):
        inst = e.request.RequestedSOPInstanceUID
        for elem in e.attribute_list:
            store[inst][elem.tag] = elem
        return 0x0000, e.attribute_list
    ae = AE(ae_title="MPPSSCP")
    ae.add_supported_context(ModalityPerformedProcedureStep)
    server = ae.start_server(
        ("127.0.0.1", 0), block=False,
        evt_handlers=[(evt.EVT_N_CREATE, h_create), (evt.EVT_N_SET, h_set)])
    try:
        node = Node(name="ris", aetitle="MPPSSCP", host="127.0.0.1",
                    port=server.server_address[1], timeout=10)
        item = {"PatientName": "DOE^JANE", "PatientID": "MRN1",
                "StudyInstanceUID": "1.2.826.0.1.3680043.8.498.2",
                "AccessionNumber": "ACC1"}
        ds, uid = mpps.build_create(item, performed_station_ae="MOD",
                                    modality="CT")
        ok, _ = mpps.mpps_create("MOD", node, ds, uid, timeout=10)
        assert ok and str(store[uid].PerformedProcedureStepStatus) == "IN PROGRESS"
        set_ds = mpps.build_set("COMPLETED", [{"SeriesInstanceUID": "1.2.3",
                                               "refs": [("1.2.840.10008.5.1.4.1.1.2",
                                                         "1.2.3.4")]}])
        ok2, _ = mpps.mpps_set("MOD", node, set_ds, uid, timeout=10)
        assert ok2 and str(store[uid].PerformedProcedureStepStatus) == "COMPLETED"
    finally:
        server.shutdown()
