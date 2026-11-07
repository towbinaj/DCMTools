"""Print2PACS engine: GUI-free DICOM logic, ported from the standalone
Print2PACS app (https://github.com/towbinaj/Print2PACS).

- ``sc_import``    : raster images / PDF -> Secondary Capture DICOM datasets
- ``dicom_import`` : re-file an existing DICOM object onto the chosen study
- ``video``        : video -> encapsulated H.264 Video Photographic DICOM
- ``ffmpeg_tools`` : locate/run the bundled or system ffmpeg
- ``reconcile``    : look up an existing study on PACS (C-FIND) -> StudyContext
- ``send``         : write datasets and store them to PACS (C-STORE)

These depend only on pydicom / pynetdicom / Pillow (+ PyMuPDF for PDF, ffmpeg
for video) and the toolkit's ``model`` / ``net`` / ``paths`` modules.
"""
