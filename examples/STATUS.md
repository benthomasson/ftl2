# Examples Status

## Completed

### Example 01: Local Execution ✅
- **Status**: WORKING
- **Files**: All created and tested
- **Modules**: ping, setup, file, shell, copy all working
- **Test**: `./run_examples.sh` completes successfully

### Modules Created ✅
Created 5 basic modules in `src/ftl2/modules/`:
- `ping.py` - Connectivity test
- `setup.py` - System fact gathering
- `shell.py` - Command execution
- `file.py` - File/directory management
- `copy.py` - File copying

### CLI Improvements ✅
- Added automatic default modules directory detection
- Fixed argument parsing to handle quoted strings (`cmd='echo hello'`)
- Uses `shlex` for proper shell-style argument parsing

### Example 02: Remote SSH ✅
- **Status**: WORKING
- **Files**: All created and tested
- **Authentication**: SSH key-based (automatic setup via setup.sh)
- **Test**: Remote execution working with ping, shell, setup, file, copy modules
- **Fixed**: Switched from password auth to SSH key auth

### Example 03: Multi-Host ✅
- **Status**: WORKING
- **Files**: All created and tested
- **Authentication**: SSH key-based (automatic setup via setup.sh)
- **Test**: Successfully pinged all 3 hosts in parallel
- **Fixed**: Inventory structure (flattened groups) and SSH key copying

## Resolved Issues

### SSH Authentication ✅ FIXED
Initially experienced "Permission denied" errors with password authentication.

**Solution**: Switched to SSH key authentication
- `setup.sh` automatically generates SSH key (~/.ssh/ftl2_example_rsa)
- Public key copied to container with proper permissions
- More secure and production-like approach
- Variables without `ansible_` prefix go into `vars` dict in inventory

**Key Learning**: In FTL2 inventory, use `ssh_private_key_file` (not `ansible_ssh_private_key_file`) since only fields starting with `ansible_` are direct host attributes

## Testing

### Local Execution
```bash
cd examples/01-local-execution
./run_examples.sh
```

**Result**: ✅ All 6 examples pass

### Remote SSH (when working)
```bash
cd examples/02-remote-ssh
./setup.sh start  # Starts container & installs Python
./run_examples.sh  # Run examples
./setup.sh stop   # Clean up
```

**Current Result**: ❌ SSH authentication fails

### SSH Integration Tests
```bash
SSH_INTEGRATION_TESTS=true pytest tests/test_ssh_integration.py -xvs
```

**Result**: ✅ All 8 tests pass (when test container is running)

## Next Steps

1. **Debug SSH Auth**: Resolve asyncssh password authentication issues
2. **Test Example 02**: Get remote SSH examples fully working
3. **Test Example 03**: Verify multi-host parallel execution
4. **Documentation**: Add troubleshooting guide for common issues
5. **CI/CD**: Add automated testing for examples

## Files Created

```
examples/
├── README.md (comprehensive guide)
├── STATUS.md (this file)
├── 01-local-execution/
│   ├── README.md
│   ├── inventory.yml
│   └── run_examples.sh ✅ WORKING
├── 02-remote-ssh/
│   ├── README.md
│   ├── docker-compose.yml
│   ├── inventory.yml
│   ├── setup.sh (with auto Python install)
│   └── run_examples.sh 🚧 AUTH ISSUE
└── 03-multi-host/
    ├── README.md
    ├── docker-compose.yml (3 containers)
    ├── inventory.yml (groups: webservers, databases)
    ├── setup.sh (multi-container mgmt)
    └── run_examples.sh 📝 UNTESTED

src/ftl2/modules/ (new)
├── ping.py ✅
├── setup.py ✅
├── shell.py ✅
├── file.py ✅
└── copy.py ✅
```

## Summary

**Working**: Local execution with all 5 modules
**Blocked**: Remote execution due to SSH auth issues
**Ready**: All files created, comprehensive documentation, good foundation

The core functionality is solid - local execution works perfectly, and the SSH integration tests prove remote execution works. The remaining issue is environment-specific authentication configuration.
