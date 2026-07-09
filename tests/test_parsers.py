from batmond.parsers.thermal import aggregate_temps

def test_aggregate_temps_empty():
    res = aggregate_temps([])
    assert res == {"soc_temp_c": None, "ssd_temp_c": None}

def test_aggregate_temps_valid():
    sensors = [
        ("PMU tdie1", 45.0),
        ("PMU tdie2", 55.0),
        ("PMU tdev1", -9000.0),
        ("NAND CH0 temp", 38.0),
        ("NAND CH1 temp", 40.0),
        ("gas gauge battery", 34.0)
    ]
    res = aggregate_temps(sensors)
    assert res == {"soc_temp_c": 55.0, "ssd_temp_c": 40.0}

def test_aggregate_temps_filtering():
    sensors = [
        ("PMU tdie1", 0.0), # Too low
        ("PMU tdie2", 130.0), # Too high
        ("PMU tdie3", 45.0), # Valid
        ("NAND temp", 150.0), # Too high
        ("NAND temp 2", -10.0), # Too low
        ("NAND CH0 temp", 35.0) # Valid
    ]
    res = aggregate_temps(sensors)
    assert res == {"soc_temp_c": 45.0, "ssd_temp_c": 35.0}

def test_aggregate_temps_missing_types():
    sensors_no_soc = [("NAND temp", 30.0)]
    assert aggregate_temps(sensors_no_soc) == {"soc_temp_c": None, "ssd_temp_c": 30.0}
    
    sensors_no_ssd = [("PMU tdie1", 50.0)]
    assert aggregate_temps(sensors_no_ssd) == {"soc_temp_c": 50.0, "ssd_temp_c": None}
