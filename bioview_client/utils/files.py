import json
from typing import List, Dict

def read_experiment_config_file(json_file) -> Dict:
    try:
        data = json.load(open(json_file, encoding = 'utf-8'))
        if not isinstance(data, dict): return {}  
        return data 
    except (json.JSONDecodeError, FileNotFoundError, PermissionError) as e:
        return {} 

'''
A typical device configuration file is expected to be a JSON respresenting
device group IDs as keys and corresponding values as whatever is required by 
the individual backend. The only thing each value dictionary needs to have
is "device_type" so that the correct device drivers are used at the backend.
'''    
REQUIRED_KEYS = ['device_type']

# Helper function to check if a provided device configuration file is valid 
def is_device_config_valid(data) -> Dict | None: 
    try: 
        for value_dicts in data.values(): 
            if not all(key in value_dicts for key in REQUIRED_KEYS): 
                return False 
        
        return True 
    except json.JSONDecodeError: 
        return False 

def read_device_config_files(json_files: List | str): 
    json_files = [json_files] if isinstance(json_files, str) else json_files

    device_config = {} 

    for json_file in json_files:         
        data = json.load(open(json_file, encoding = 'utf-8'))
        if is_device_config_valid(data): 
            device_config.update(data)
    
    return device_config