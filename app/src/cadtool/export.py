"""Mesh export functions for cadtool."""

from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Message import Message_ProgressRange
from OCP.RWGltf import RWGltf_CafWriter
from OCP.TColStd import TColStd_IndexedDataMapOfStringString
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool


def export_glb(shape, output_path, linear_deflection=0.1):
    """Export an OCP TopoDS_Shape to binary glTF (.glb).

    Tessellates the shape, wraps it in an XCAF document, and writes
    via RWGltf_CafWriter.
    """
    # Tessellate
    BRepMesh_IncrementalMesh(shape, linear_deflection)

    # Create XCAF document and add shape
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    shape_tool.AddShape(shape)

    # Write binary glTF
    writer = RWGltf_CafWriter(TCollection_AsciiString(str(output_path)), True)
    writer.Perform(doc, TColStd_IndexedDataMapOfStringString(), Message_ProgressRange())
