#!/usr/bin/env python3
"""
Flask server for STEP assembly using build123d
Exposes REST API endpoint for assembling STEP files with transformations
Accepts uploaded STEP files for remote server deployment
Also provides STP to DXF conversion
https://github.com/gumyr/build123d
"""

import os
import uuid
import json
import traceback
import zipfile
import shutil
from flask import Flask, request, jsonify, send_file
from werkzeug.utils import secure_filename
from stp_assembler import STEPAssembler
from stp_to_dxf_converter import STPtoDXFConverter

app = Flask(__name__)

# Configuration
OUTPUT_FOLDER = '/app/output'
UPLOAD_FOLDER = '/app/uploads'  # Temporary folder for uploaded STP files
MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500MB max total upload size

app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Ensure directories exist
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'step-assembler'
    }), 200


@app.route('/assemble', methods=['POST'])
def assemble_step_files():
    """
    Assemble multiple STEP files into a single STEP assembly with transformations
    
    Request:
        - Method: POST
        - Content-Type: multipart/form-data
        - Body:
            - assemblyData: JSON string with products array and fileName
            - files: Multiple STP files (field name: "stp_<productId>")
        
    Response:
        - Success: Returns compressed ZIP file containing STEP assembly
        - Error: Returns JSON with error message
    """
    # Create a unique session folder for this assembly request
    session_id = str(uuid.uuid4())
    session_folder = os.path.join(app.config['UPLOAD_FOLDER'], session_id)
    os.makedirs(session_folder, exist_ok=True)
    
    try:
        # Parse assembly data from form
        assembly_data_str = request.form.get('assemblyData')
        if not assembly_data_str:
            return jsonify({
                'error': 'No assembly data provided',
                'message': 'Please provide assemblyData field with products array'
            }), 400
        
        try:
            assembly_data = json.loads(assembly_data_str)
        except json.JSONDecodeError as e:
            return jsonify({
                'error': 'Invalid JSON in assemblyData',
                'message': str(e)
            }), 400
        
        products = assembly_data.get('products', [])
        file_name = assembly_data.get('fileName', 'Assembly')
        
        if not products or not isinstance(products, list):
            return jsonify({
                'error': 'No products provided',
                'message': 'Please provide an array of products to assemble'
            }), 400
        
        print(f"Assembling {len(products)} products into STEP file")
        print(f"Session folder: {session_folder}")
        
        # Save uploaded STP files to session folder
        uploaded_files = []
        for key in request.files:
            if key.startswith('stp_'):
                file = request.files[key]
                product_id = key[4:]  # Remove 'stp_' prefix
                filename = secure_filename(f"{product_id}.stp")
                file_path = os.path.join(session_folder, filename)
                file.save(file_path)
                uploaded_files.append(product_id)
                print(f"Saved uploaded file: {filename}")
        
        print(f"Uploaded {len(uploaded_files)} STP files")
        
        # Check that all required STEP files exist in session folder
        missing_files = []
        for product in products:
            product_id = product.get('productId', '')
            stp_path = os.path.join(session_folder, f"{product_id}.stp")
            if not os.path.exists(stp_path):
                missing_files.append(product_id)
        
        if missing_files:
            return jsonify({
                'error': 'Missing STEP files',
                'message': f'STEP files not uploaded for products: {", ".join(missing_files)}',
                'missingProducts': missing_files
            }), 400
        
        # Generate unique output filename
        output_filename = f"{session_id}_{file_name}.stp"
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
        
        print(f"Output path: {output_path}")
        
        # Create assembler and perform assembly using session folder
        assembler = STEPAssembler(session_folder, output_path)
        assembler.load_assembly_data(products)
        success = assembler.assemble()
        
        if not success:
            return jsonify({
                'error': 'Assembly failed',
                'message': 'Failed to create STEP assembly from provided products'
            }), 500
        
        # Check if output file was created
        if not os.path.exists(output_path):
            return jsonify({
                'error': 'Output file not created',
                'message': 'Assembly completed but output file was not generated'
            }), 500
        
        # Create compressed ZIP file with maximum compression
        zip_filename = f"{session_id}_{file_name}.zip"
        zip_path = os.path.join(app.config['OUTPUT_FOLDER'], zip_filename)
        
        print(f"Creating compressed ZIP: {zip_path}")
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
            zipf.write(output_path, arcname=f"{file_name}.stp")
        
        # Get file sizes for logging
        original_size = os.path.getsize(output_path)
        compressed_size = os.path.getsize(zip_path)
        compression_ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
        
        print(f"Original size: {original_size:,} bytes")
        print(f"Compressed size: {compressed_size:,} bytes")
        print(f"Compression ratio: {compression_ratio:.1f}%")
        
        # Clean up uncompressed STEP file
        try:
            os.remove(output_path)
        except Exception as e:
            print(f"Warning: Could not remove STEP file: {e}")
        
        # Return the compressed ZIP file
        response = send_file(
            zip_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"{file_name}.zip"
        )
        
        # Schedule cleanup after sending
        @response.call_on_close
        def cleanup():
            # Clean up ZIP file
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                    print(f"Cleaned up ZIP file: {zip_path}")
            except Exception as e:
                print(f"Warning: Could not remove ZIP file: {e}")
            
            # Clean up session folder with uploaded files
            try:
                if os.path.exists(session_folder):
                    shutil.rmtree(session_folder)
                    print(f"Cleaned up session folder: {session_folder}")
            except Exception as e:
                print(f"Warning: Could not remove session folder: {e}")
        
        return response
        
    except Exception as e:
        print(f"Error during assembly: {str(e)}")
        traceback.print_exc()
        
        # Clean up session folder on error
        try:
            if os.path.exists(session_folder):
                shutil.rmtree(session_folder)
        except:
            pass
        
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@app.route('/convert-to-dxf', methods=['POST'])
def convert_stp_to_dxf():
    """
    Convert a STEP file to DXF format with 2D projections
    
    Request:
        - Method: POST
        - Content-Type: multipart/form-data
        - Body:
            - file: The STP file to convert
            - views: Optional comma-separated list of views (top,front,right,section,iso)
            - section_z: Optional Z-height for section view (in mm)
        
    Response:
        - Success: Returns DXF file
        - Error: Returns JSON with error message
    """
    # Create a unique session folder for this request
    session_id = str(uuid.uuid4())
    session_folder = os.path.join(app.config['UPLOAD_FOLDER'], session_id)
    os.makedirs(session_folder, exist_ok=True)
    
    try:
        # Check if file is provided
        if 'file' not in request.files:
            return jsonify({
                'error': 'No file provided',
                'message': 'Please provide a STEP file with field name "file"'
            }), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({
                'error': 'No file selected',
                'message': 'Please select a STEP file to convert'
            }), 400
        
        # Validate file extension
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(('.stp', '.step')):
            return jsonify({
                'error': 'Invalid file type',
                'message': 'Please provide a STEP file (.stp or .step)'
            }), 400
        
        # Save the uploaded file
        input_path = os.path.join(session_folder, filename)
        file.save(input_path)
        
        print(f"Received STEP file for DXF conversion: {filename}")
        
        # Get optional parameters
        views_str = request.form.get('views', 'top,front,right')
        views = [v.strip() for v in views_str.split(',') if v.strip()]
        
        section_z = None
        if 'section_z' in request.form:
            try:
                section_z = float(request.form.get('section_z'))
            except ValueError:
                pass
        
        # Get output name (without extension)
        output_name = os.path.splitext(filename)[0]
        
        # Convert to DXF
        converter = STPtoDXFConverter(session_folder)
        success, output_path, message = converter.convert(
            input_path,
            output_name=output_name,
            views=views,
            section_z=section_z
        )
        
        if not success:
            return jsonify({
                'error': 'Conversion failed',
                'message': message
            }), 500
        
        # Check if output file exists
        if not os.path.exists(output_path):
            return jsonify({
                'error': 'Output file not created',
                'message': 'DXF conversion completed but file was not generated'
            }), 500
        
        # Return the DXF file
        dxf_filename = f"{output_name}.dxf"
        
        response = send_file(
            output_path,
            mimetype='application/dxf',
            as_attachment=True,
            download_name=dxf_filename
        )
        
        # Schedule cleanup after sending
        @response.call_on_close
        def cleanup():
            try:
                if os.path.exists(session_folder):
                    shutil.rmtree(session_folder)
                    print(f"Cleaned up session folder: {session_folder}")
            except Exception as e:
                print(f"Warning: Could not remove session folder: {e}")
        
        return response
        
    except Exception as e:
        print(f"Error during DXF conversion: {str(e)}")
        traceback.print_exc()
        
        # Clean up session folder on error
        try:
            if os.path.exists(session_folder):
                shutil.rmtree(session_folder)
        except:
            pass
        
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@app.route('/convert-assembly-to-dxf', methods=['POST'])
def convert_assembly_to_dxf():
    """
    Convert an assembled STEP file to DXF format
    First assembles the STEP files, then converts the assembly to DXF
    
    Request:
        - Method: POST
        - Content-Type: multipart/form-data
        - Body:
            - assemblyData: JSON string with products array and fileName
            - files: Multiple STP files (field name: "stp_<productId>")
            - views: Optional comma-separated list of views (top,front,right,section,iso)
        
    Response:
        - Success: Returns ZIP file containing both STEP assembly and DXF file
        - Error: Returns JSON with error message
    """
    # Create a unique session folder
    session_id = str(uuid.uuid4())
    session_folder = os.path.join(app.config['UPLOAD_FOLDER'], session_id)
    os.makedirs(session_folder, exist_ok=True)
    
    try:
        # Parse assembly data from form
        assembly_data_str = request.form.get('assemblyData')
        if not assembly_data_str:
            return jsonify({
                'error': 'No assembly data provided',
                'message': 'Please provide assemblyData field with products array'
            }), 400
        
        try:
            assembly_data = json.loads(assembly_data_str)
        except json.JSONDecodeError as e:
            return jsonify({
                'error': 'Invalid JSON in assemblyData',
                'message': str(e)
            }), 400
        
        products = assembly_data.get('products', [])
        file_name = assembly_data.get('fileName', 'Assembly')
        
        if not products or not isinstance(products, list):
            return jsonify({
                'error': 'No products provided',
                'message': 'Please provide an array of products to assemble'
            }), 400
        
        print(f"Converting assembly of {len(products)} products to DXF")
        
        # Save uploaded STP files to session folder
        uploaded_files = []
        for key in request.files:
            if key.startswith('stp_'):
                file = request.files[key]
                product_id = key[4:]  # Remove 'stp_' prefix
                filename = secure_filename(f"{product_id}.stp")
                file_path = os.path.join(session_folder, filename)
                file.save(file_path)
                uploaded_files.append(product_id)
        
        # Check that all required STEP files exist
        missing_files = []
        for product in products:
            product_id = product.get('productId', '')
            stp_path = os.path.join(session_folder, f"{product_id}.stp")
            if not os.path.exists(stp_path):
                missing_files.append(product_id)
        
        if missing_files:
            return jsonify({
                'error': 'Missing STEP files',
                'message': f'STEP files not uploaded for products: {", ".join(missing_files)}',
                'missingProducts': missing_files
            }), 400
        
        # Step 1: Create STEP assembly
        assembly_stp_path = os.path.join(session_folder, f"{file_name}.stp")
        
        assembler = STEPAssembler(session_folder, assembly_stp_path)
        assembler.load_assembly_data(products)
        success = assembler.assemble()
        
        if not success or not os.path.exists(assembly_stp_path):
            return jsonify({
                'error': 'Assembly failed',
                'message': 'Failed to create STEP assembly from provided products'
            }), 500
        
        # Step 2: Convert assembly to DXF
        views_str = request.form.get('views', 'top,front,right')
        views = [v.strip() for v in views_str.split(',') if v.strip()]
        
        converter = STPtoDXFConverter(session_folder)
        dxf_success, dxf_path, dxf_message = converter.convert(
            assembly_stp_path,
            output_name=file_name,
            views=views
        )
        
        if not dxf_success:
            print(f"DXF conversion warning: {dxf_message}")
            # Continue without DXF - we'll still return the STEP file
        
        # Step 3: Create ZIP with both files
        zip_filename = f"{session_id}_{file_name}.zip"
        zip_path = os.path.join(app.config['OUTPUT_FOLDER'], zip_filename)
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
            # Add STEP assembly
            zipf.write(assembly_stp_path, arcname=f"{file_name}.stp")
            
            # Add DXF if conversion succeeded
            if dxf_success and os.path.exists(dxf_path):
                zipf.write(dxf_path, arcname=f"{file_name}.dxf")
        
        # Return the ZIP file
        response = send_file(
            zip_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"{file_name}.zip"
        )
        
        # Schedule cleanup after sending
        @response.call_on_close
        def cleanup():
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                if os.path.exists(session_folder):
                    shutil.rmtree(session_folder)
                    print(f"Cleaned up session folder: {session_folder}")
            except Exception as e:
                print(f"Warning: Could not clean up: {e}")
        
        return response
        
    except Exception as e:
        print(f"Error during assembly DXF conversion: {str(e)}")
        traceback.print_exc()
        
        try:
            if os.path.exists(session_folder):
                shutil.rmtree(session_folder)
        except:
            pass
        
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with API information"""
    return jsonify({
        'service': 'STEP Assembler API',
        'version': '2.1.0',
        'library': 'build123d (https://github.com/gumyr/build123d)',
        'endpoints': {
            'POST /assemble': {
                'description': 'Assemble multiple STEP files with transformations',
                'content_type': 'multipart/form-data',
                'parameters': {
                    'assemblyData': 'JSON string with products array and fileName',
                    'stp_<productId>': 'STP file for each product (e.g., stp_382090006301)'
                },
                'response': 'Compressed ZIP file containing assembled STEP file'
            },
            'POST /convert-to-dxf': {
                'description': 'Convert a single STEP file to DXF format',
                'content_type': 'multipart/form-data',
                'parameters': {
                    'file': 'The STEP file to convert',
                    'views': 'Optional: comma-separated views (top,front,right,section,iso)',
                    'section_z': 'Optional: Z-height for section view in mm'
                },
                'response': 'DXF file with 2D projections'
            },
            'POST /convert-assembly-to-dxf': {
                'description': 'Assemble STEP files and convert to DXF',
                'content_type': 'multipart/form-data',
                'parameters': {
                    'assemblyData': 'JSON string with products array and fileName',
                    'stp_<productId>': 'STP file for each product',
                    'views': 'Optional: comma-separated views (top,front,right,section,iso)'
                },
                'response': 'ZIP file containing STEP assembly and DXF file'
            },
            'GET /health': {
                'description': 'Health check endpoint',
                'response': 'Service status'
            }
        },
        'usage': {
            'assemble': '''curl -X POST -F "assemblyData={...}" -F "stp_382090006301=@file1.stp" http://localhost:5001/assemble -o assembly.zip''',
            'convert_to_dxf': '''curl -X POST -F "file=@model.stp" -F "views=top,front,right" http://localhost:5001/convert-to-dxf -o model.dxf''',
            'convert_assembly_to_dxf': '''curl -X POST -F "assemblyData={...}" -F "stp_382090006301=@file1.stp" -F "views=top,front" http://localhost:5001/convert-assembly-to-dxf -o assembly.zip'''
        },
        'compression': {
            'level': 9,
            'description': 'Maximum compression (ZIP_DEFLATED with compresslevel=9)'
        }
    }), 200


@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file too large error"""
    return jsonify({
        'error': 'File too large',
        'message': f'Maximum total upload size is {MAX_CONTENT_LENGTH // (1024 * 1024)}MB'
    }), 413


@app.errorhandler(500)
def internal_server_error(error):
    """Handle internal server errors"""
    return jsonify({
        'error': 'Internal server error',
        'message': 'An unexpected error occurred'
    }), 500


if __name__ == '__main__':
    print("Starting STEP Assembler Server...")
    print(f"Output folder: {OUTPUT_FOLDER}")
    print(f"Upload folder: {UPLOAD_FOLDER}")
    print("Server running on http://0.0.0.0:5001")
    
    app.run(host='0.0.0.0', port=5001, debug=False)
