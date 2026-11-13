from kd_crosscheck import expand_notation, designation_type, pair_key

def test_range():
    out, unknown = expand_notation('1KM1...1KM8;2KM1...2KM8')
    assert len(out) == 16 and not unknown

def test_matching_name():
    a='ТНБВ.656451.162.Э3 Щит 6.5-NDB-09.pdf'
    b='ТНБВ.656451.162.ПЭ3 Щит 6.5-NDB-09.pdf'
    assert pair_key(a) == pair_key(b)
    assert designation_type(a) == 'Э3' and designation_type(b) == 'ПЭ3'

def test_lookalike_letters():
    from kd_crosscheck import normalized
    assert normalized('KМ0') == normalized('KM0')
