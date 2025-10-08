from bioview_common import Configuration


def is_dict_of_dicts(data):
    """
    Checks if the given 'data' is a dictionary where all its values are also dictionaries.
    """
    if not isinstance(data, dict):
        return False  # Not a dictionary at the top level

    if not data:
        return True  # An empty dictionary can be considered a dict of dicts

    return all(isinstance(value, dict) for value in data.values())


def group_config_to_dict(group_config):
    if not is_dict_of_dicts(group_config):
        return {}

    group_config_dict = {}
    for gid, group_cfg in group_config.items():
        group_config_dict[gid] = {
            dev_id: dev_cfg.to_dict() if isinstance(dev_cfg, Configuration) else dev_cfg
            for dev_id, dev_cfg in group_cfg.items()
        }

    return group_config_dict
