def is_dict_of_dicts(data):
    """
    Checks if the given 'data' is a dictionary where all its values are also dictionaries.
    """
    if not isinstance(data, dict):
        return False  # Not a dictionary at the top level

    if not data:
        return True  # An empty dictionary can be considered a dict of dicts

    return all(isinstance(value, dict) for value in data.values())
