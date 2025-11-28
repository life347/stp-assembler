#!/usr/bin/env python3
"""
STP to DXF Converter using build123d and ezdxf
Converts STEP files to DXF format with 2D projections
https://github.com/gumyr/build123d
"""

import os
import math
from pathlib import Path
from typing import Optional, Tuple, List

import ezdxf
from ezdxf import units

from build123d import import_step

# OCP imports for edge extraction and projection
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_UniformDeflection
from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Circle, GeomAbs_Ellipse, GeomAbs_BSplineCurve
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS


class STPtoDXFConverter:
    """Converts STEP files to DXF format with 2D projections"""
    
    def __init__(self, output_folder: str):
        """
        Initialize the converter
        
        Args:
            output_folder: Directory for output DXF files
        """
        self.output_folder = Path(output_folder)
        self.output_folder.mkdir(parents=True, exist_ok=True)
        
    def convert(
        self,
        stp_path: str,
        output_name: Optional[str] = None,
        views: List[str] = None,
        section_z: Optional[float] = None
    ) -> Tuple[bool, str, str]:
        """
        Convert a STEP file to DXF format
        
        Args:
            stp_path: Path to the input STEP file
            output_name: Name for the output file (without extension)
            views: List of views to include: 'top', 'front', 'right', 'iso'
            section_z: Z-height for section view (in mm), if None uses center
            
        Returns:
            Tuple of (success: bool, output_path: str, message: str)
        """
        if views is None:
            views = ['top', 'front', 'right']
            
        stp_path = Path(stp_path)
        
        if not stp_path.exists():
            return False, "", f"STEP file not found: {stp_path}"
        
        if output_name is None:
            output_name = stp_path.stem
            
        output_path = self.output_folder / f"{output_name}.dxf"
        
        try:
            print(f"Loading STEP file: {stp_path}")
            
            # Import the STEP file
            model = import_step(str(stp_path))
            
            if model is None:
                return False, "", "Failed to import STEP file"
            
            # Get the OCC shape
            if hasattr(model, 'wrapped'):
                occ_shape = model.wrapped
            else:
                occ_shape = model
            
            print(f"STEP file loaded successfully")
            
            # Get bounding box using build123d
            try:
                bbox = model.bounding_box()
                xmin, ymin, zmin = bbox.min.X, bbox.min.Y, bbox.min.Z
                xmax, ymax, zmax = bbox.max.X, bbox.max.Y, bbox.max.Z
            except Exception as e:
                print(f"Warning: Could not get bounding box: {e}")
                xmin, ymin, zmin = -100, -100, -100
                xmax, ymax, zmax = 100, 100, 100
            
            center = ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)
            size = (xmax - xmin, ymax - ymin, zmax - zmin)
            max_dim = max(size) if max(size) > 0 else 100
            
            print(f"Model bounding box: min=({xmin:.2f}, {ymin:.2f}, {zmin:.2f}), max=({xmax:.2f}, {ymax:.2f}, {zmax:.2f})")
            print(f"Model size: {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f}")
            
            # Create DXF document
            doc = ezdxf.new('R2013')
            doc.units = units.MM
            msp = doc.modelspace()
            
            # Spacing between views
            spacing = max_dim * 1.5
            view_positions = {
                'top': (0, 0),
                'front': (spacing, 0),
                'right': (spacing * 2, 0),
                'iso': (spacing * 3, 0),
            }
            
            edges_added = 0
            
            # Generate each requested view
            for view in views:
                try:
                    print(f"Generating {view} view...")
                    
                    # Create layer for this view
                    layer_name = f"VIEW_{view.upper()}"
                    doc.layers.add(layer_name)
                    
                    # Get view offset
                    offset_x, offset_y = view_positions.get(view, (0, 0))
                    
                    # Extract and project edges
                    count = self._add_projected_edges(
                        msp, occ_shape, view, layer_name,
                        offset_x, offset_y, center
                    )
                    edges_added += count
                    print(f"  {view} view: added {count} edges")
                    
                except Exception as e:
                    print(f"Warning: Failed to generate {view} view: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Write the DXF file
            print(f"Writing DXF file: {output_path}")
            doc.saveas(str(output_path))
            
            if output_path.exists():
                file_size = output_path.stat().st_size
                print(f"DXF file created successfully: {file_size} bytes, {edges_added} total edges")
                return True, str(output_path), f"Successfully converted to DXF ({file_size} bytes, {edges_added} edges)"
            else:
                return False, "", "DXF file was not created"
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, "", f"Conversion failed: {str(e)}"
    
    def _add_projected_edges(
        self,
        msp,
        shape,
        view: str,
        layer_name: str,
        offset_x: float,
        offset_y: float,
        center: Tuple[float, float, float]
    ) -> int:
        """
        Extract edges from shape and project them to 2D for the given view
        
        Returns:
            Number of edges added
        """
        edges_added = 0
        
        # Extract all edges from the shape
        explorer = TopExp_Explorer(shape, TopAbs_EDGE)
        
        while explorer.More():
            edge = TopoDS.Edge_s(explorer.Current())
            
            try:
                # Get curve adaptor
                adaptor = BRepAdaptor_Curve(edge)
                curve_type = adaptor.GetType()
                
                # Get discretized points
                points_3d = self._discretize_edge(adaptor)
                
                if len(points_3d) >= 2:
                    # Project points to 2D based on view
                    points_2d = self._project_points(points_3d, view, center)
                    
                    # Apply offset
                    points_2d = [(p[0] + offset_x, p[1] + offset_y) for p in points_2d]
                    
                    # Add to DXF
                    if len(points_2d) == 2:
                        # Simple line
                        msp.add_line(
                            points_2d[0],
                            points_2d[1],
                            dxfattribs={'layer': layer_name}
                        )
                    else:
                        # Polyline for curves
                        msp.add_lwpolyline(
                            points_2d,
                            dxfattribs={'layer': layer_name}
                        )
                    edges_added += 1
                    
            except Exception as e:
                pass  # Skip problematic edges
            
            explorer.Next()
        
        return edges_added
    
    def _discretize_edge(self, adaptor: BRepAdaptor_Curve, deflection: float = 0.5) -> List[Tuple[float, float, float]]:
        """
        Discretize an edge into a list of 3D points
        
        Args:
            adaptor: BRepAdaptor_Curve for the edge
            deflection: Maximum chord deviation
            
        Returns:
            List of (x, y, z) tuples
        """
        points = []
        
        try:
            curve_type = adaptor.GetType()
            first = adaptor.FirstParameter()
            last = adaptor.LastParameter()
            
            # For lines, just get start and end
            if curve_type == GeomAbs_Line:
                p1 = adaptor.Value(first)
                p2 = adaptor.Value(last)
                points.append((p1.X(), p1.Y(), p1.Z()))
                points.append((p2.X(), p2.Y(), p2.Z()))
            else:
                # For curves, use uniform deflection discretization
                try:
                    discretizer = GCPnts_UniformDeflection(adaptor, deflection)
                    
                    if discretizer.IsDone():
                        num_points = discretizer.NbPoints()
                        for i in range(1, num_points + 1):
                            pnt = discretizer.Value(i)
                            points.append((pnt.X(), pnt.Y(), pnt.Z()))
                    else:
                        # Fallback to parameter-based sampling
                        num_samples = 20
                        for i in range(num_samples + 1):
                            t = first + (last - first) * i / num_samples
                            pnt = adaptor.Value(t)
                            points.append((pnt.X(), pnt.Y(), pnt.Z()))
                except:
                    # Ultimate fallback - just start and end
                    p1 = adaptor.Value(first)
                    p2 = adaptor.Value(last)
                    points.append((p1.X(), p1.Y(), p1.Z()))
                    points.append((p2.X(), p2.Y(), p2.Z()))
                
        except Exception as e:
            pass
        
        return points
    
    def _project_points(
        self,
        points_3d: List[Tuple[float, float, float]],
        view: str,
        center: Tuple[float, float, float]
    ) -> List[Tuple[float, float]]:
        """
        Project 3D points to 2D based on view type
        
        Args:
            points_3d: List of (x, y, z) 3D points
            view: View type ('top', 'front', 'right', 'iso')
            center: Center of the model for positioning
            
        Returns:
            List of (x, y) 2D points
        """
        points_2d = []
        cx, cy, cz = center
        
        for x, y, z in points_3d:
            if view == 'top':
                # Top view: looking down Z axis, XY plane
                px = x - cx
                py = y - cy
            elif view == 'front':
                # Front view: looking along Y axis, XZ plane
                px = x - cx
                py = z - cz
            elif view == 'right':
                # Right view: looking along X axis, YZ plane
                px = y - cy
                py = z - cz
            elif view == 'iso':
                # Isometric projection
                # Standard isometric angles
                angle_x = math.radians(30)
                angle_z = math.radians(45)
                
                # Transform to isometric
                x_rel = x - cx
                y_rel = y - cy
                z_rel = z - cz
                
                px = x_rel * math.cos(angle_z) - y_rel * math.sin(angle_z)
                py = x_rel * math.sin(angle_z) * math.sin(angle_x) + \
                     y_rel * math.cos(angle_z) * math.sin(angle_x) + \
                     z_rel * math.cos(angle_x)
            else:
                # Default to top view
                px = x - cx
                py = y - cy
            
            points_2d.append((px, py))
        
        return points_2d


def convert_stp_to_dxf(
    stp_path: str,
    output_path: str,
    views: List[str] = None
) -> Tuple[bool, str]:
    """
    Convenience function to convert a single STEP file to DXF
    
    Args:
        stp_path: Path to the input STEP file
        output_path: Path for the output DXF file
        views: List of views to include
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    output_dir = os.path.dirname(output_path)
    output_name = os.path.splitext(os.path.basename(output_path))[0]
    
    converter = STPtoDXFConverter(output_dir)
    success, _, message = converter.convert(
        stp_path,
        output_name=output_name,
        views=views
    )
    
    return success, message


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python stp_to_dxf_converter.py <input.stp> <output.dxf> [views]")
        print("  views: comma-separated list of views (top,front,right,iso)")
        print("  default views: top,front,right")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    views = sys.argv[3].split(',') if len(sys.argv) > 3 else None
    
    success, message = convert_stp_to_dxf(input_path, output_path, views)
    print(message)
    sys.exit(0 if success else 1)
