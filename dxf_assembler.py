#!/usr/bin/env python3
"""
DXF File Assembler using ezdxf
Merges multiple DXF files into a single assembly with transformations.
Takes assembly data (positions) and creates a combined DXF file.
Products are processed in the order provided (sequence matters for assembly).
"""

import sys
import json
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import ezdxf
from ezdxf.addons import Importer
from ezdxf import bbox


class DXFProduct:
    """Represents a product in the DXF assembly with its transformation data"""
    
    def __init__(self, data: dict):
        self.id = data.get('id', '')
        self.product_id = data.get('productId', '')
        self.name = data.get('name', '')
        
        # Position in meters (from frontend)
        pos = data.get('position', [0, 0, 0])
        self.position = (float(pos[0]), float(pos[1]), float(pos[2]))
        
        # Rotation in radians (from frontend) - for future use
        rot = data.get('rotation', [0, 0, 0])
        self.rotation = (float(rot[0]), float(rot[1]), float(rot[2]))
        
        # Scale factors - for future use
        scale = data.get('scale', [1, 1, 1])
        self.scale = (float(scale[0]), float(scale[1]), float(scale[2]))
        
        # Parent reference for hierarchy
        self.parent_id = data.get('parentId')
        self.child_position = data.get('childPosition')
        self.level = data.get('level', 0)
    
    @property
    def z_offset_mm(self) -> float:
        """Get Z position in millimeters (used for X translation in 2D)"""
        return self.position[2] * 1000.0
    
    @property
    def y_offset_mm(self) -> float:
        """Get Y position in millimeters (used for Y translation in 2D)"""
        return self.position[1] * 1000.0


class DXFAssembler:
    """Assembles DXF files into a single document with transformations"""
    
    def __init__(self, dxf_base_path: str, output_path: str):
        """
        Initialize the assembler
        
        Args:
            dxf_base_path: Base directory containing DXF files
            output_path: Path for the output assembly DXF file
        """
        self.dxf_base_path = Path(dxf_base_path)
        self.output_path = output_path
        self.products: List[DXFProduct] = []
        self.output_doc = None
        self.msp_output = None
        
    def load_assembly_data(self, assembly_data: List[dict]) -> None:
        """
        Load assembly data from JSON list
        IMPORTANT: Products are processed in the order provided (sequence matters)
        """
        self.products = [DXFProduct(p) for p in assembly_data]
        print(f"Loaded {len(self.products)} products for DXF assembly")
        print(f"Assembly sequence:")
        for i, p in enumerate(self.products):
            print(f"  {i+1}. {p.product_id} (z_offset={p.z_offset_mm:.1f}mm)")
        
    def _get_dxf_path(self, product_id: str) -> Path:
        """Get the DXF file path for a product"""
        return self.dxf_base_path / f"{product_id}.dxf"
    
    def _get_bbox(self, msp) -> Optional[Tuple[float, float, float, float]]:
        """
        Get bounding box (min_x, min_y, max_x, max_y) of modelspace entities
        
        Returns:
            Tuple of (min_x, min_y, max_x, max_y) or None if calculation fails
        """
        try:
            # Use ezdxf.bbox module for proper bounding box calculation
            cache = bbox.Cache()
            bounding_box = bbox.extents(msp, cache=cache)
            if bounding_box.has_data:
                return (bounding_box.extmin.x, bounding_box.extmin.y, 
                        bounding_box.extmax.x, bounding_box.extmax.y)
        except Exception as e:
            print(f"    bbox.extents failed: {e}")
        
        # Fallback: manually calculate from entities
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        
        for entity in msp:
            try:
                entity_bbox = bbox.extents([entity])
                if entity_bbox.has_data:
                    min_x = min(min_x, entity_bbox.extmin.x)
                    min_y = min(min_y, entity_bbox.extmin.y)
                    max_x = max(max_x, entity_bbox.extmax.x)
                    max_y = max(max_y, entity_bbox.extmax.y)
            except:
                pass
        
        if min_x != float('inf'):
            return (min_x, min_y, max_x, max_y)
        return None
    
    def assemble(self) -> bool:
        """
        Perform the DXF assembly
        
        Products are processed in the order they were loaded (sequence matters).
        The FIRST product is the reference (no translation).
        Other products are translated RELATIVE to the first product's Z position.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            print(f"\nStarting DXF assembly with {len(self.products)} products")
            print(f"DXF files base path: {self.dxf_base_path}")
            
            if not self.products:
                print("Error: No products to assemble")
                return False
            
            # Create a new DXF document for the assembly
            print("\nCreating new DXF assembly document...")
            self.output_doc = ezdxf.new('R2010')  # R2010 for better compatibility
            self.msp_output = self.output_doc.modelspace()
            
            # The FIRST product is the reference point (no translation)
            # All other products are positioned RELATIVE to the first product
            first_product_z_offset = self.products[0].z_offset_mm
            print(f"\nReference product: {self.products[0].product_id}")
            print(f"Reference Z offset: {first_product_z_offset:.2f}mm")
            print(f"All other products will be positioned relative to this.\n")
            
            # Process each product in the provided order (sequence matters!)
            for i, product in enumerate(self.products):
                dxf_path = self._get_dxf_path(product.product_id)
                
                if not dxf_path.exists():
                    print(f"Warning: DXF file not found: {dxf_path}")
                    continue
                
                print(f"\nProcessing [{i+1}/{len(self.products)}]: {product.name} ({product.product_id})")
                print(f"  Position (m): {product.position}")
                print(f"  Z offset (mm): {product.z_offset_mm:.2f}")
                
                try:
                    # Read the source DXF file
                    source_doc = ezdxf.readfile(str(dxf_path))
                    msp_source = source_doc.modelspace()
                    
                    # Get bounding box info
                    bbox_data = self._get_bbox(msp_source)
                    if bbox_data:
                        min_x, min_y, max_x, max_y = bbox_data
                        center_x = (min_x + max_x) / 2
                        center_y = (min_y + max_y) / 2
                        width = max_x - min_x
                        height = max_y - min_y
                        print(f"  BBox: ({min_x:.1f}, {min_y:.1f}) to ({max_x:.1f}, {max_y:.1f})")
                        print(f"  Size: {width:.1f} x {height:.1f}, Center: ({center_x:.1f}, {center_y:.1f})")
                    else:
                        print(f"  Warning: Could not calculate bounding box")
                    
                    # Count entities before import
                    entities_before = len(self.msp_output)
                    
                    # Use Importer to copy entities from source to output
                    importer = Importer(source_doc, self.output_doc)
                    importer.import_modelspace()
                    importer.finalize()
                    
                    # Count entities after import
                    entities_after = len(self.msp_output)
                    new_entities_count = entities_after - entities_before
                    
                    print(f"  Imported {new_entities_count} entities")
                    
                    # Calculate translation RELATIVE to the first product
                    # Formula matches AssemblerContext.tsx positioning logic:
                    # - First product is the anchor (leftmost), stays at X=0
                    # - Other products are positioned to the RIGHT of it
                    # - If first product has higher Z, other products with lower Z go to the right
                    # translate_x = first_z_offset - product_z_offset
                    #   First (z=48): 48 - 48 = 0 (anchor)
                    #   Second (z=0): 48 - 0 = +48 (to the right)
                    #   Third (z=-80): 48 - (-80) = +128 (further right)
                    translate_x = first_product_z_offset - product.z_offset_mm
                    translate_y = product.y_offset_mm  # Y stays as Y
                    
                    print(f"  Relative X translation: {first_product_z_offset:.1f} - {product.z_offset_mm:.1f} = {translate_x:.1f}mm")
                    print(f"  Applying translation: X={translate_x:.1f}mm, Y={translate_y:.1f}mm")
                    
                    # Translate only the newly imported entities
                    entities_list = list(self.msp_output)
                    translated_count = 0
                    for entity in entities_list[-new_entities_count:]:
                        try:
                            entity.translate(translate_x, translate_y, 0)
                            translated_count += 1
                        except Exception as e:
                            print(f"    Warning: Could not translate entity {entity.dxftype()}: {e}")
                    
                    print(f"  Successfully translated {translated_count} entities")
                    
                except Exception as e:
                    print(f"  Error processing {product.product_id}: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            if len(self.msp_output) == 0:
                print("Error: No entities were imported")
                return False
            
            # Save the merged DXF file
            print(f"\nSaving assembly to: {self.output_path}")
            self.output_doc.saveas(self.output_path)
            
            total_entities = len(self.msp_output)
            print(f"DXF assembly completed successfully!")
            print(f"Total entities in output: {total_entities}")
            
            return True
            
        except Exception as e:
            print(f"Error during DXF assembly: {str(e)}")
            import traceback
            traceback.print_exc()
            return False


def main():
    """Main entry point for command line usage"""
    if len(sys.argv) < 3:
        print("Usage: python dxf_assembler.py <assembly_data.json> <output.dxf> [dxf_base_path]")
        print("  assembly_data.json: JSON file with assembly product data")
        print("  output.dxf: Output DXF file path")
        print("  dxf_base_path: Base directory for DXF files (default: current directory)")
        print("\nIMPORTANT: Products are processed in the order they appear in the JSON.")
        print("           The sequence determines the assembly order.")
        print("\nExample:")
        print("  python dxf_assembler.py assembly.json output.dxf /path/to/dxf/files")
        print("\nJSON format example:")
        print('''  [
    {
      "id": "product-123",
      "productId": "416500105020",
      "name": "Product Name",
      "position": [0, 0, 0.048],
      "rotation": [0, 0, 0],
      "scale": [1, 1, 1]
    },
    {
      "id": "product-456",
      "productId": "656905000800",
      "name": "Second Product",
      "position": [0, 0, 0],
      "rotation": [0, 0, 0],
      "scale": [1, 1, 1]
    }
  ]''')
        sys.exit(1)
    
    assembly_json_path = sys.argv[1]
    output_path = sys.argv[2]
    dxf_base_path = sys.argv[3] if len(sys.argv) > 3 else "."
    
    # Load assembly data
    try:
        with open(assembly_json_path, 'r') as f:
            assembly_data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON file: {e}")
        sys.exit(1)
    
    # Create assembler and run
    assembler = DXFAssembler(dxf_base_path, output_path)
    assembler.load_assembly_data(assembly_data)
    success = assembler.assemble()
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
