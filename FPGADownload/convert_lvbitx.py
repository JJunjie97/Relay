import os
import glob
import base64
import hashlib
import xml.etree.ElementTree as ET
import shutil

def process_lvbitx(filepath):
    print(f"Processing {filepath}...")
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except Exception as e:
        print(f"  Error parsing XML: {e}")
        return

    md5_elem = root.find('BitstreamMD5')
    bitstream_elem = root.find('Bitstream')

    if md5_elem is None or bitstream_elem is None:
        print("  Skipping: Bitstream or BitstreamMD5 not found in the XML.")
        return

    expected_md5 = md5_elem.text.strip().lower()
    b64_data = bitstream_elem.text.strip()

    try:
        bit_data = base64.b64decode(b64_data)
    except Exception as e:
        print(f"  Error decoding base64: {e}")
        return

    actual_md5 = hashlib.md5(bit_data).hexdigest().lower()
    if actual_md5 != expected_md5:
        print("  MD5 mismatch!")
        print(f"    Expected: {expected_md5}")
        print(f"    Actual:   {actual_md5}")
        return
    else:
        print(f"  MD5 check passed ({actual_md5}).")

    basename = os.path.basename(filepath)
    name_without_ext = os.path.splitext(basename)[0]
    dir_path = os.path.join(os.path.dirname(filepath), name_without_ext)

    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

    bit_filepath = os.path.join(dir_path, f"{name_without_ext}.bit")
    with open(bit_filepath, 'wb') as f:
        f.write(bit_data)
    print(f"  Saved bitstream to {bit_filepath}")

    dest_lvbitx = os.path.join(dir_path, basename)
    shutil.move(filepath, dest_lvbitx)
    print(f"  Moved {basename} to {dir_path}/")
    print(f"  Successfully processed {basename}.\n")

def main():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    lvbitx_files = glob.glob(os.path.join(current_dir, "*.lvbitx"))
    
    if not lvbitx_files:
        print("No .lvbitx files found in the current directory.")
        return
        
    for filepath in lvbitx_files:
        process_lvbitx(filepath)

if __name__ == "__main__":
    main()
