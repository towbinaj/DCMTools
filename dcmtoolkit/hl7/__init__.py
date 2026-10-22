"""Minimal HL7 v2 over MLLP - send only (mimics SmartHL7.Sender).

Not a full HL7 stack: just enough to frame a message in MLLP, send it over TCP,
read the ACK, and do light message-type awareness (ADT / ORU / ORM).
"""
