targets = [
    "pineapple",
]

la_variants = [
    "consolidate",
    "gki",
    "perf",
]

def get_all_la_variants():
    return [(t, v) for t in targets for v in la_variants]

def get_all_le_variants():
    return []

def get_all_lxc_variants():
    return []

def get_all_lunch_target_base_target_variants():
    return []

def get_all_variants():
    return get_all_la_variants()
