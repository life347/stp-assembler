#!/usr/bin/env python3
"""
STEP File Assembler using build123d
Creates assemblies from individual STEP files with transformations.
Takes assembly data (positions, rotations, scales) and creates a combined STEP file.
https://github.com/gumyr/build123d
"""

import sys
import json
import math
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from build123d import (
    Compound,
    import_step,
    export_step,
    Location,
    Rotation,
)

# OCP imports for advanced transformations
from OCP.gp import gp_Trsf, gp_Vec, gp_Ax1, gp_Pnt, gp_Dir
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape
from OCP.BRep import BRep_Builder


class AssemblyProduct:
    """Represents a product in the assembly with its transformation data"""
    
    def __init__(self, data: dict):
        self.id = data.get('id', '')
        self.product_id = data.get('productId', '')
        self.name = data.get('name', '')
        
        # Position in meters (from frontend)
        pos = data.get('position', [0, 0, 0])
        self.position = (float(pos[0]), float(pos[1]), float(pos[2]))
        
        # Rotation in radians (from frontend)
        rot = data.get('rotation', [0, 0, 0])
        self.rotation = (float(rot[0]), float(rot[1]), float(rot[2]))
        
        # Scale factors
        scale = data.get('scale', [1, 1, 1])
        self.scale = (float(scale[0]), float(scale[1]), float(scale[2]))
        
        # Parent reference for hierarchy
        self.parent_id = data.get('parentId')
        self.child_position = data.get('childPosition')
        self.level = data.get('level', 0)


class STEPAssembler:
    """Assembles STEP files into a single compound with transformations"""
    
    def __init__(self, stp_base_path: str, output_path: str):
        """
        Initialize the assembler
        
        Args:
            stp_base_path: Base directory containing STEP files
            output_path: Path for the output assembly STEP file
        """
        self.stp_base_path = Path(stp_base_path)
        self.output_path = output_path
        self.products: List[AssemblyProduct] = []
        self.shapes: List[TopoDS_Shape] = []
        
    def load_assembly_data(self, assembly_data: List[dict]) -> None:
        """Load assembly data from JSON list"""
        self.products = [AssemblyProduct(p) for p in assembly_data]
        print(f"Loaded {len(self.products)} products for assembly")
        
    def _get_stp_path(self, product_id: str) -> Path:
        """Get the STEP file path for a product"""
        return self.stp_base_path / f"{product_id}.stp"
    
    def _meters_to_mm(self, meters: float) -> float:
        """Convert meters to millimeters (STEP files typically use mm)"""
        return meters * 1000.0
    
    def _radians_to_degrees(self, radians: float) -> float:
        """Convert radians to degrees"""
        return math.degrees(radians)
    
    def _create_transformation(self, product: AssemblyProduct) -> gp_Trsf:
        """
        Create an OpenCascade transformation for a product
        
        The transformation is applied in the order:
        1. Scale (if not uniform 1,1,1)
        2. Rotation (X, Y, Z Euler angles)
        3. Translation (position)
        """
        trsf = gp_Trsf()
        
        # Convert position from meters to millimeters
        tx = self._meters_to_mm(product.position[0])
        ty = self._meters_to_mm(product.position[1])
        tz = self._meters_to_mm(product.position[2])
        
        # Get rotations in radians
        rx, ry, rz = product.rotation
        
        # Apply rotations (Euler angles XYZ order)
        # First create rotation transformations
        if rx != 0:
            rot_x = gp_Trsf()
            rot_x.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), rx)
            trsf = trsf.Multiplied(rot_x)
            
        if ry != 0:
            rot_y = gp_Trsf()
            rot_y.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 1, 0)), ry)
            trsf = trsf.Multiplied(rot_y)
            
        if rz != 0:
            rot_z = gp_Trsf()
            rot_z.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), rz)
            trsf = trsf.Multiplied(rot_z)
        
        # Apply translation
        trans = gp_Trsf()
        trans.SetTranslation(gp_Vec(tx, ty, tz))
        trsf = trsf.Multiplied(trans)
        
        return trsf
    
    def _apply_transformation(self, shape: TopoDS_Shape, trsf: gp_Trsf) -> TopoDS_Shape:
        """Apply transformation to a shape"""
        transformer = BRepBuilderAPI_Transform(shape, trsf, True)
        return transformer.Shape()
    
    def assemble(self) -> bool:
        """
        Perform the assembly
        
        Returns:
            True if successful, False otherwise
        """
        try:
            print(f"Starting assembly with {len(self.products)} products")
            print(f"STEP files base path: {self.stp_base_path}")
            
            # Load and transform each product
            for product in self.products:
                stp_path = self._get_stp_path(product.product_id)
                
                if not stp_path.exists():
                    print(f"Warning: STEP file not found: {stp_path}")
                    continue
                
                print(f"\nProcessing: {product.name} ({product.product_id})")
                print(f"  Position (m): {product.position}")
                print(f"  Position (mm): ({self._meters_to_mm(product.position[0]):.2f}, "
                      f"{self._meters_to_mm(product.position[1]):.2f}, "
                      f"{self._meters_to_mm(product.position[2]):.2f})")
                print(f"  Rotation (rad): {product.rotation}")
                print(f"  Rotation (deg): ({self._radians_to_degrees(product.rotation[0]):.1f}, "
                      f"{self._radians_to_degrees(product.rotation[1]):.1f}, "
                      f"{self._radians_to_degrees(product.rotation[2]):.1f})")
                print(f"  Child position: {product.child_position}")
                
                try:
                    # Import the STEP file using build123d
                    imported = import_step(str(stp_path))
                    
                    # Get the underlying OCC shape
                    if hasattr(imported, 'wrapped'):
                        occ_shape = imported.wrapped
                    else:
                        occ_shape = imported
                    
                    # Create and apply transformation
                    trsf = self._create_transformation(product)
                    transformed_shape = self._apply_transformation(occ_shape, trsf)
                    
                    self.shapes.append(transformed_shape)
                    print(f"  Successfully added to assembly")
                    
                except Exception as e:
                    print(f"  Error processing {product.product_id}: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            if not self.shapes:
                print("Error: No shapes were successfully loaded")
                return False
            
            # Create compound from all shapes
            print(f"\nCreating compound from {len(self.shapes)} shapes...")
            builder = BRep_Builder()
            compound = TopoDS_Compound()
            builder.MakeCompound(compound)
            
            for shape in self.shapes:
                builder.Add(compound, shape)
            
            # Export the compound
            print(f"Exporting assembly to: {self.output_path}")
            b123d_compound = Compound(compound)
            export_step(b123d_compound, self.output_path)
            
            print("Assembly completed successfully!")
            return True
            
        except Exception as e:
            print(f"Error during assembly: {str(e)}")
            import traceback
            traceback.print_exc()
            return False


def main():
    """Main entry point for command line usage"""
    if len(sys.argv) < 3:
        print("Usage: python stp_assembler.py <assembly_data.json> <output.stp> [stp_base_path]")
        print("  assembly_data.json: JSON file with assembly product data")
        print("  output.stp: Output STEP file path")
        print("  stp_base_path: Base directory for STEP files (default: current directory)")
        print("\nExample:")
        print("  python stp_assembler.py assembly.json output.stp /path/to/stp/files")
        print("\nJSON format example:")
        print('''  [
    {
      "id": "product-123",
      "productId": "382090006301",
      "name": "Product Name",
      "position": [0, 0, 0],
      "rotation": [0, 0, 0],
      "scale": [1, 1, 1],
      "parentId": null,
      "childPosition": null,
      "level": 0
    }
  ]''')
        sys.exit(1)
    
    assembly_json_path = sys.argv[1]
    output_path = sys.argv[2]
    stp_base_path = sys.argv[3] if len(sys.argv) > 3 else "."
    
    # Load assembly data
    try:
        with open(assembly_json_path, 'r') as f:
            assembly_data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON file: {e}")
        sys.exit(1)
    
    # Create assembler and run
    assembler = STEPAssembler(stp_base_path, output_path)
    assembler.load_assembly_data(assembly_data)
    success = assembler.assemble()
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
