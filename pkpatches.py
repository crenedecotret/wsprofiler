from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, BooleanVar
import subprocess
import xml.etree.ElementTree as ET
import os

def update_command_preview():
    target_name = target_entry.get().strip()
    patch_count = patch_entry.get().strip()
    white_patches = white_entry.get().strip()
    black_patches = black_entry.get().strip()
    gray_patches = gray_entry.get().strip()
    use_precond = precond_var.get()
    precond_path = precond_path_var.get().strip()

    cmd = ["targen", "-v", "-d2", "-G"]
    if patch_count:
        cmd.append(f"-f{patch_count}")
    if white_patches:
        cmd.append(f"-e{white_patches}")
    if black_patches:
        cmd.append(f"-B{black_patches}")
    if gray_var.get() and gray_patches:
        cmd.append(f"-g{gray_patches}")
    if use_precond and precond_path:
        cmd.extend(["-c", precond_path, "-N0.75"])
    if target_name:
        cmd.append(target_name)

    preview_text = format_cmd_for_display(cmd)
    command_preview.configure(state="normal")  # Temporarily enable editing
    command_preview.delete("1.0", tk.END)
    command_preview.insert(tk.END, preview_text)
    command_preview.configure(state="disabled")  # Lock it again
 

def validate_patch_count(original, reordered, fallback_label="original RGB data"):
    if len(reordered) < len(original):
        print(f"⚠️ Warning: patch count mismatch. Expected {len(original)}, got {len(reordered)}. Falling back to {fallback_label}.")
        return [p[:3] for p in original]  # Strip tags if needed
    return reordered
 
    
def format_cmd_for_display(cmd_list):
    return " ".join(f'"{arg}"' if ' ' in arg else arg for arg in cmd_list)
    
def toggle_gray_entry():
    state = tk.NORMAL if gray_var.get() else tk.DISABLED
    gray_entry.config(state=state)
    scramble_checkbox.config(state=state)
    if gray_var.get():
        scramble_var.set(True)  # Re-check when enabling grayscale
    else:
        scramble_var.set(False)  # Uncheck when disabling grayscale
    update_command_preview()



def select_precond_file():
    path = filedialog.askopenfilename(title="Select ICC/ICM Profile",
                                      filetypes=[("ICC/ICM files", "*.icc *.icm")])
    if path:
        precond_path_var.set(path)
        precond_checkbox.config(state=tk.NORMAL)
        precond_var.set(True)
        update_command_preview()

def select_working_folder():
    folder = filedialog.askdirectory(title="Select Working Folder")
    if folder:
        working_folder_var.set(folder)
        update_command_preview()
        
def scramble_max_distance(patches):
    import math

    def color_distance(c1, c2):
        return math.sqrt((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2 + (c1[2]-c2[2])**2)

    remaining = patches[:]
    scrambled = [remaining.pop(0)]

    while remaining:
        def avg_distance(patch):
            return sum(color_distance(patch, placed) for placed in scrambled) / len(scrambled)

        farthest = max(remaining, key=avg_distance)
        scrambled.append(farthest)
        remaining.remove(farthest)

    return scrambled



def write_colorport_cgats(input_path, output_path):
    import re

    def scale_rgb(value):
        return str(round(float(value) * 2.55))

    with open(input_path, 'r') as f:
        lines = f.readlines()

    header = []
    data_lines = []
    in_data = False
    data_started = False

    for line in lines:
        stripped = line.strip()
        if stripped == "BEGIN_DATA" and not data_started:
            in_data = True
            data_started = True
            continue
        if stripped == "END_DATA" and in_data:
            in_data = False
            break
        if in_data:
            parts = stripped.split()
            if len(parts) >= 4:
                try:
                    scaled_rgb = [str(scale_rgb(v)) for v in parts[1:4]]
                    xyz = parts[4:7] if len(parts) >= 7 else ["0", "0", "0"]
                    data_lines.append(f"{parts[0]} {' '.join(scaled_rgb)} {' '.join(xyz)}")
                except ValueError:
                    continue
        elif not data_started:
            header.append(line)

    # Clean up header
    header = [re.sub(r'COLOR_REP\s+".*"', 'COLOR_REP "RGB"', h) for h in header]
    header = [re.sub(r'TOTAL_INK_LIMIT\s+".*"', '', h) for h in header]

    with open(output_path, 'w') as f:
        for line in header:
            f.write(line)
        f.write("NUMBER_OF_SETS {}\n".format(len(data_lines)))
        f.write("BEGIN_DATA\n")
        for line in data_lines:
            f.write(line + "\n")
        f.write("END_DATA\n")
		
		
    
    

def run_targen(target_path, patch_count, white_patches, black_patches, gray_patches, use_precond, precond_path, include_gray):
    cmd = ["targen", "-v", "-d2", "-G"]
    cmd.append(f"-f{patch_count}")
    cmd.append(f"-e{white_patches}")
    cmd.append(f"-B{black_patches}")
    if include_gray and gray_patches:
        cmd.append(f"-g{gray_patches}")
    if use_precond and precond_path:
        cmd.extend(["-c", precond_path, "-N0.75"])
    cmd.append(target_path)

    output_text.delete("1.0", tk.END)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output_text.insert(tk.END, f"Run started at {timestamp}\n")
    output_text.see(tk.END)

    output_text.insert(tk.END, "Running command:\n" + format_cmd_for_display(cmd) + "\n\n")
    output_text.see(tk.END)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        output_text.insert(tk.END, result.stdout)
        if result.stderr:
            output_text.insert(tk.END, "\nErrors:\n" + result.stderr)
        if result.returncode != 0:
            messagebox.showerror("Error", "targen failed. See output window for details.")
            return False
    except Exception as e:
        output_text.insert(tk.END, f"\nException: {e}")
        messagebox.showerror("Error", f"targen execution failed:\n{e}")
        return False

    return True


def parse_ti1(filename):
    rgb_data = []
    in_data = False
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if line == "BEGIN_DATA":
                in_data = True
                continue
            if line == "END_DATA":
                break
            if in_data:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        r = float(parts[1])
                        g = float(parts[2])
                        b = float(parts[3])
                        if r == g == b == 100.0:
                            patch_type = "white"
                        elif r == g == b == 0.0:
                            patch_type = "black"
                        elif r == g == b:
                            patch_type = "gray"
                        else:
                            patch_type = "color"
                        rgb_data.append((r, g, b, patch_type))
                    except ValueError:
                        continue
    return rgb_data


def interleave_bw_and_optional_grays(rgb_data, randomize_grays=False):
    import random
    ofps = [p[:3] for p in rgb_data if p[3] == "color"]
    whites = [p[:3] for p in rgb_data if p[3] == "white"]
    blacks = [p[:3] for p in rgb_data if p[3] == "black"]
    grays = [p[:3] for p in rgb_data if p[3] == "gray"]

    # Interleave black and white patches
    interleaved_refs = []
    for w, b in zip(whites, blacks):
        interleaved_refs.extend([w, b])

    result = []
    ref_index = 0
    insert_every = max(1, len(ofps) // len(interleaved_refs)) if interleaved_refs else len(ofps)

    for i, patch in enumerate(ofps):
        result.append(patch)
        if ref_index < len(interleaved_refs) and (i + 1) % insert_every == 0:
            result.append(interleaved_refs[ref_index])
            ref_index += 1

    result.extend(interleaved_refs[ref_index:])

    # Handle grayscale ramp
    if randomize_grays:
        random.shuffle(grays)
        # Interleave grays into OFSP+BW
        gray_index = 0
        insert_every = max(1, len(result) // len(grays)) if grays else len(result)
        final_result = []
        for i, patch in enumerate(result):
            final_result.append(patch)
            if gray_index < len(grays) and (i + 1) % insert_every == 0:
                final_result.append(grays[gray_index])
                gray_index += 1
        final_result.extend(grays[gray_index:])
        return final_result
    else:
        result.extend(grays)
        return result
		
		


def interleave_white_black(rgb_data):
    whites = [p[:3] for p in rgb_data if p[3] == "white"]
    blacks = [p[:3] for p in rgb_data if p[3] == "black"]
    interleaved = []
    for w, b in zip(whites, blacks):
        interleaved.extend([w, b])
    return interleaved
	


def interleave_white_black_into_ofps(rgb_data):
    ofps = [p[:3] for p in rgb_data if p[3] == "color"]
    grays = [p[:3] for p in rgb_data if p[3] == "gray"]
    whites = [p[:3] for p in rgb_data if p[3] == "white"]
    blacks = [p[:3] for p in rgb_data if p[3] == "black"]

    interleaved_refs = []
    for w, b in zip(whites, blacks):
        interleaved_refs.extend([w, b])

    result = []
    ref_index = 0
    insert_every = max(1, len(ofps) // len(interleaved_refs))

    for i, patch in enumerate(ofps):
        result.append(patch)
        if ref_index < len(interleaved_refs) and (i + 1) % insert_every == 0:
            result.append(interleaved_refs[ref_index])
            ref_index += 1

    result.extend(interleaved_refs[ref_index:])
    result.extend(grays)  # append grayscale ramp at the end

    return result
	
    
    
def interleave_reference_patches(rgb_data):
    import random
    colors = [p[:3] for p in rgb_data if p[3] == "color"]
    grays = [p[:3] for p in rgb_data if p[3] == "gray"]
    whites = [p[:3] for p in rgb_data if p[3] == "white"]
    blacks = [p[:3] for p in rgb_data if p[3] == "black"]

    reference = whites + blacks + grays
    random.shuffle(reference)

    result = []
    ref_index = 0
    insert_every = max(1, len(colors) // len(reference))

    for i, patch in enumerate(colors):
        result.append(patch)
        if ref_index < len(reference) and (i + 1) % insert_every == 0:
            result.append(reference[ref_index])
            ref_index += 1

    result.extend(reference[ref_index:])
    return result
    
    
	

def interleave_grays(rgb_data):
    import random
    colors = [p[:3] for p in rgb_data if p[3] == "color"]
    references = [p[:3] for p in rgb_data if p[3] in ("gray", "white", "black")]
    random.shuffle(references)

    result = []
    ref_index = 0
    insert_every = max(1, len(colors) // len(references)) if references else len(colors)

    for i, patch in enumerate(colors):
        result.append(patch)
        if ref_index < len(references) and (i + 1) % insert_every == 0:
            result.append(references[ref_index])
            ref_index += 1

    result.extend(references[ref_index:])
    return result


	

def scale_rgb(r, g, b):
    return round(r * 2.55), round(g * 2.55), round(b * 2.55)


def build_pxf(rgb_list, output_file):
    import xml.etree.ElementTree as ET
    from datetime import datetime, timezone
    import os

    # ✅ Clamp and integer-convert RGB values
    def scale_rgb(r, g, b):
        clamp = lambda x: max(0, min(255, int(round(x * 2.55))))
        return clamp(r), clamp(g), clamp(b)

    # ✅ Indentation helper for pretty-printing
    def indent(elem, level=0):
        i = "\n" + "  " * level
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            for child in elem:
                indent(child, level + 1)
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i

    ns = "http://colorexchangeformat.com/CxF3-core"
    xsi = "http://www.w3.org/2001/XMLSchema-instance"
    ET.register_namespace('cc', ns)
    ET.register_namespace('xsi', xsi)

    # ✅ Declare only xsi manually — cc is handled by register_namespace
    root = ET.Element(f'{{{ns}}}CxF', {
        f'xmlns:xsi': xsi
    })

    # ✅ Generate timestamp for uniqueness
    desc_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # File metadata
    file_info = ET.SubElement(root, f'{{{ns}}}FileInformation')
    ET.SubElement(file_info, f'{{{ns}}}Creator').text = f"PKPatches Generator – {desc_stamp}"
    ET.SubElement(file_info, f'{{{ns}}}CreationDate').text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ET.SubElement(file_info, f'{{{ns}}}Description').text = f"Custom Patch Set {desc_stamp}"

    # Patch data
    resources = ET.SubElement(root, f'{{{ns}}}Resources')
    obj_collection = ET.SubElement(resources, f'{{{ns}}}ObjectCollection')

    for i, (r, g, b) in enumerate(rgb_list, start=1):
        r255, g255, b255 = scale_rgb(r, g, b)

        obj = ET.SubElement(obj_collection, f'{{{ns}}}Object',
                            ObjectType="Target", Name=f"Target{i}", Id=f"c{i}")
        ET.SubElement(obj, f'{{{ns}}}CreationDate').text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        dev_values = ET.SubElement(obj, f'{{{ns}}}DeviceColorValues')
        color_rgb = ET.SubElement(dev_values, f'{{{ns}}}ColorRGB', ColorSpecification="sRGB")
        ET.SubElement(color_rgb, f'{{{ns}}}R').text = str(r255)
        ET.SubElement(color_rgb, f'{{{ns}}}G').text = str(g255)
        ET.SubElement(color_rgb, f'{{{ns}}}B').text = str(b255)

    # ✅ Apply indentation before writing
    indent(root)

    try:
        temp_path = output_file + ".tmp"
        with open(temp_path, 'wb') as f:
            tree = ET.ElementTree(root)
            tree.write(f, encoding='utf-8', xml_declaration=True)
            f.flush()
            os.fsync(f.fileno())

        # ✅ Normalize line endings to CRLF
        with open(temp_path, 'rb') as f:
            content = f.read().replace(b'\n', b'\r\n')
        with open(temp_path, 'wb') as f:
            f.write(content)

        os.replace(temp_path, output_file)

        # Tail check
        with open(output_file, 'rb') as f:
            tail = f.read()[-300:]
            print("Last 300 bytes of file:", tail.decode('utf-8', errors='replace'))

        # ✅ Validate structure and patch count
        tree = ET.parse(output_file)
        root = tree.getroot()
        objects = root.findall(".//{http://colorexchangeformat.com/CxF3-core}Object")
        print("PXF file is complete and well-formed.")
        print("Objects in file:", len(objects))
        print(f"Total patches written: {len(rgb_list)}")

        if len(objects) != len(rgb_list):
            print(f"⚠️ Warning: Expected {len(rgb_list)} patches, found {len(objects)} in PXF.")

        print(f"PXF file written successfully: {output_file}")

    except ET.ParseError as e:
        print("PXF file is malformed:", e)
        raise
    except Exception as e:
        print("PXF write failed:", e)
        raise
        
        

        
        
        

def validate_patch_count(original, reordered, label="reordered"):
    if len(reordered) < len(original):
        print(f"⚠️ Warning: {label} patch count mismatch. Expected {len(original)}, got {len(reordered)}. Falling back to original RGB data.")
        return [p[:3] for p in original]
    return reordered

def validate_patch_count(original, reordered, label="reordered"):
    if len(reordered) < len(original):
        print(f"⚠️ Warning: {label} patch count mismatch. Expected {len(original)}, got {len(reordered)}. Falling back to original RGB data.")
        return [p[:3] for p in original]
    return reordered

def generate_and_convert():
    target_name = target_entry.get().strip()
    patch_count = patch_entry.get().strip()
    white_patches = white_entry.get().strip()
    black_patches = black_entry.get().strip()
    gray_patches = gray_entry.get().strip()
    use_precond = precond_var.get()
    precond_path = precond_path_var.get()
    working_folder = working_folder_var.get()

    if not target_name:
        messagebox.showerror("Error", "Please enter a target name.")
        return
    if not working_folder:
        messagebox.showerror("Error", "Please select a working folder.")
        return

    try:
        patch_count = int(patch_count)
        white_patches = int(white_patches)
        black_patches = int(black_patches)
        gray_patches = int(gray_patches) if gray_patches else None
    except ValueError:
        messagebox.showerror("Error", "Patch counts must be integers.")
        return

    target_path = os.path.join(working_folder, target_name)
    ti1_file = target_path + ".ti1"
    pxf_file = target_path + ".pxf"
    cgats_file = target_path + ".cgats"

    if use_precond and not precond_path:
        messagebox.showwarning("Warning", "Preconditioning is enabled but no ICC/ICM file is selected.\nIt will be ignored.")

    success = run_targen(target_path, patch_count, white_patches, black_patches, gray_patches, use_precond, precond_path, gray_var.get())
    if not success:
        return

    if not os.path.exists(ti1_file):
        messagebox.showerror("Error", f"TI1 file not found:\n{ti1_file}")
        return

    rgb_data = parse_ti1(ti1_file)
    print("Original RGB count:", len(rgb_data))

    # Determine whether to blend grayscale into OFPS
    blend_grayscale = scramble_var.get()

    # Backup original TI1 if scrambling
    if blend_grayscale:
        unscrambled_file = target_path + "_unscrambled.ti1"
        try:
            with open(ti1_file, 'r') as original, open(unscrambled_file, 'w') as backup:
                backup.write(original.read())
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save unscrambled TI1:\n{e}")
            return

    # Reorder patches with correct grayscale blending
    rgb_values = interleave_bw_and_optional_grays(rgb_data, randomize_grays=blend_grayscale)
    rgb_values = validate_patch_count(rgb_data, rgb_values, "interleaved")

    try:
        with open(ti1_file, 'r') as original:
            lines = original.readlines()

        begin_index = next(i for i, line in enumerate(lines) if line.strip() == "BEGIN_DATA") + 1
        end_index = next(i for i, line in enumerate(lines) if line.strip() == "END_DATA")

        reordered_lines = [
            f"{i} {r:.4f} {g:.4f} {b:.4f} 0.000000 0.000000 0.000000\n"
            for i, (r, g, b) in enumerate(rgb_values, start=1)
        ]

        lines[begin_index:end_index] = reordered_lines

        with open(ti1_file, 'w') as updated:
            updated.writelines(lines)

        if blend_grayscale:
            output_text.insert(tk.END, f"TI1 patch order randomized with grayscale blended. Original saved as:\n{unscrambled_file}\n")
        else:
            output_text.insert(tk.END, "TI1 patch order updated with grayscale appended.\n")
        output_text.see(tk.END)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to update TI1 patch order:\n{e}")
        return

    print("Post-interleave count:", len(rgb_values))

    # ✅ Patch validation block
    print("Validating patch list...")
    print("Final patch count:", len(rgb_values))
    for i, p in enumerate(rgb_values, 1):
        if not isinstance(p, (list, tuple)) or len(p) != 3:
            print(f"❌ Malformed patch at index {i}: {p}")
        elif not all(isinstance(x, (int, float)) for x in p):
            print(f"❌ Non-numeric value at index {i}: {p}")
        elif any(x is None for x in p):
            print(f"❌ None value at index {i}: {p}")
    print("Patch list validation complete.")

    # Build PXF file
    try:
        build_pxf(rgb_values, pxf_file)
        output_text.insert(tk.END, f"\nPXF file saved with {len(rgb_values)} patches:\n{pxf_file}\n")
        output_text.see(tk.END)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to write PXF file:\n{e}")
        return

    # Optionally write CGATS
    if cgats_var.get():
        try:
            write_colorport_cgats(ti1_file, cgats_file)
            output_text.insert(tk.END, f"CGATS file saved:\n{cgats_file}\n")
            output_text.see(tk.END)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to write CGATS file:\n{e}")
            return

    messagebox.showinfo("Success", f"PXF file saved:\n{pxf_file}")	
    
    

# GUI setup
root = tk.Tk()
root.title("PKPatches ArgyllCMS and X-Rite Patch Generator")
root.geometry("460x700")

def bind_updates(widget):
    widget.bind("<KeyRelease>", lambda event: update_command_preview())
    widget.bind("<FocusOut>", lambda event: update_command_preview())

tk.Label(root, text="Target name (no extension):").pack()
target_entry = tk.Entry(root)
target_entry.insert(0, "mytarget")
target_entry.pack()
bind_updates(target_entry)

tk.Label(root, text="Total patch count:").pack()
patch_entry = tk.Entry(root)
patch_entry.insert(0, "400")
patch_entry.pack()
bind_updates(patch_entry)

tk.Label(root, text="White patch count:").pack()
white_entry = tk.Entry(root)
white_entry.insert(0, "4")
white_entry.pack()
bind_updates(white_entry)

tk.Label(root, text="Black patch count:").pack()
black_entry = tk.Entry(root)
black_entry.insert(0, "4")  # Default value
black_entry.pack()
bind_updates(black_entry)

gray_var = BooleanVar(value=True)
gray_checkbox = tk.Checkbutton(root, text="Include grayscale patches", variable=gray_var,
                               command=toggle_gray_entry)
gray_checkbox.pack()
gray_entry = tk.Entry(root)
gray_entry.insert(0, "51")
gray_entry.pack()
bind_updates(gray_entry)


scramble_var = BooleanVar(value=True)
scramble_checkbox = tk.Checkbutton(root, text="Blend grayscale within OFPS patches", variable=scramble_var)
scramble_checkbox.pack()


tk.Label(root, text="").pack(pady=4)
               
precond_var = BooleanVar()
precond_checkbox = tk.Checkbutton(root, text="Use preconditioning profile", variable=precond_var,
                                  command=update_command_preview, state=tk.DISABLED)
precond_checkbox.pack()
               

precond_path_var = tk.StringVar()
tk.Button(root, text="Select ICC/ICM file", command=select_precond_file).pack()
tk.Label(root, textvariable=precond_path_var, wraplength=360).pack()

tk.Label(root, text="").pack(pady=4)  # Adds vertical space

working_folder_var = tk.StringVar(value=os.path.dirname(os.path.abspath(__file__)))
tk.Button(root, text="Select Working Folder", command=select_working_folder).pack()
tk.Label(root, textvariable=working_folder_var, wraplength=360).pack()

cgats_var = BooleanVar(value=True)
cgats_checkbox = tk.Checkbutton(root, text="Save a copy as a ColorPort CGATS file", variable=cgats_var)
cgats_checkbox.pack()

tk.Button(root, text="Generate files", command=generate_and_convert).pack(pady=20)

tk.Label(root, text="Generated targen command:").pack()
command_preview = tk.Text(root, height=3, width=60, wrap=tk.WORD)
command_preview.pack()
command_preview.configure(state="normal")
update_command_preview()

# Scrollable output window
output_frame = tk.Frame(root)
output_frame.pack(fill=tk.BOTH, expand=True)

output_scrollbar = tk.Scrollbar(output_frame)
output_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

output_text = tk.Text(output_frame, height=12, wrap=tk.WORD, yscrollcommand=output_scrollbar.set)
output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
output_scrollbar.config(command=output_text.yview)

# Launch the GUI
root.mainloop()
