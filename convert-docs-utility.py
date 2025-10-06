#!/usr/bin/env python3

import json
import subprocess
import sys
from pathlib import Path


#TODO to anyone reading this, you'll need pandoc available in the environment before
# running it
def convert_html_to_markdown(html_content):
    try:
        result = subprocess.run(
            ['pandoc', '-f', 'html', '-t', 'markdown', ' --columns=80'],
            input=html_content,
            text=True,
            capture_output=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error converting HTML: {e}")
        return html_content

def process_json_recursively(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "smithy.api#documentation" and isinstance(value, str):
                obj[key] = convert_html_to_markdown(value)
            else:
                process_json_recursively(value)
    elif isinstance(obj, list):
        for item in obj:
            process_json_recursively(item)

def main():
    #TODO for anyone reading this, you'll need to change this so it's the right path
    # (or just not hardcoded)
    json_file = Path("/Users/remiss/PycharmProjects/sagemaker-runtime-smithy/model/model.json")
    
    if not json_file.exists():
        print(f"Error: {json_file} not found")
        sys.exit(1)
    
    print("Loading JSON file...")
    with open(json_file, 'r') as f:
        data = json.load(f)
    
    print("Converting HTML documentation to markdown...")
    process_json_recursively(data)
    
    with open(json_file, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print("Conversion complete!")

if __name__ == "__main__":
    main()
