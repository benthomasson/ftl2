from ftl2.inventory import expand_host_range

def test_numeric_range():
    assert expand_host_range('www[01:05].example.com') == [
        'www01.example.com', 'www02.example.com', 'www03.example.com',
        'www04.example.com', 'www05.example.com'
    ]

def test_numeric_stride():
    assert expand_host_range('www[01:09:2].example.com') == [
        'www01.example.com', 'www03.example.com', 'www05.example.com',
        'www07.example.com', 'www09.example.com'
    ]

def test_alpha_range():
    assert expand_host_range('db-[a:f].example.com') == [
        'db-a.example.com', 'db-b.example.com', 'db-c.example.com',
        'db-d.example.com', 'db-e.example.com', 'db-f.example.com'
    ]

def test_no_range():
    assert expand_host_range('web01.example.com') == ['web01.example.com']

def test_leading_zeros():
    result = expand_host_range('www[01:50].example.com')
    assert result[0] == 'www01.example.com'
    assert result[-1] == 'www50.example.com'
    assert len(result) == 50

def test_cartesian_product():
    result = expand_host_range('[a:b]-[1:2]')
    assert result == ['a-1', 'a-2', 'b-1', 'b-2']

def test_yaml_inventory_expansion():
    import yaml
    from ftl2.inventory import _load_inventory_yaml

    data = yaml.safe_load("""
webservers:
  hosts:
    www[01:03].example.com:
      ansible_user: deploy
databases:
  hosts:
    db-[a:c].example.com:
""")
    inventory = _load_inventory_yaml(data)
    all_hosts = inventory.get_all_hosts()
    assert 'www01.example.com' in all_hosts
    assert 'www02.example.com' in all_hosts
    assert 'www03.example.com' in all_hosts
    assert all_hosts['www01.example.com'].ansible_user == 'deploy'
    assert 'db-a.example.com' in all_hosts
    assert 'db-b.example.com' in all_hosts
    assert 'db-c.example.com' in all_hosts

if __name__ == '__main__':
    test_numeric_range()
    test_numeric_stride()
    test_alpha_range()
    test_no_range()
    test_leading_zeros()
    test_cartesian_product()
    test_yaml_inventory_expansion()
    print('All tests passed!')
