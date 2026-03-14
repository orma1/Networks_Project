from pathlib import Path
import yaml
import os


class ConfigLoader:
    def __init__(self, config_filename="resolver_config.yaml") -> None:
        self.project_root = Path(__file__).parent.parent
        self.config_path = self.project_root / "configs" / config_filename
        self._settings = self._load_config()
        self._ensure_data_dir()

    def _load_config(self) -> dict:
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with open(self.config_path, 'r') as f:
            # SafeLoader is recommended for security. Fallback to empty dict if file is blank.
            config = yaml.safe_load(f) or {}

        # --- Schema Validation ---
        required_sections = ['server', 'upstream', 'behavior']
        missing_sections = [sec for sec in required_sections if sec not in config]

        if missing_sections:
            raise ValueError(
                f"[FATAL] Invalid configuration in {self.config_path}. "
                f"Missing required top-level sections: {', '.join(missing_sections)}"
            )
        
        # Initialize optional sections so downstream .get() chains don't fail
        if 'storage' not in config:
            config['storage'] = {}

        return config

    def _ensure_data_dir(self):
        """Creates the data folder if it doesn't exist."""
        # Get the directory part of the cache file path
        cache_path_str = self.cache_file_path
        cache_path = Path(cache_path_str)
        if not cache_path.parent.exists():
            cache_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Getters for "server" section ---
    @property
    def bind_ip(self): 
        return self._settings['server'].get('bind_ip', '127.0.0.2')
    
    @property
    def bind_port(self): 
        return self._settings['server'].get('bind_port', 53)

    @property
    def buffer_size(self): 
        return self._settings['server'].get('buffer_size', 4096)

    # --- Getters for "upstream" section ---
    @property
    def root_server_ip(self): 
        return self._settings['upstream'].get('root_server_ip', '127.0.0.3')
    
    @property
    def root_server_port(self):
        return self._settings['upstream'].get('root_server_port', 53)

    @property
    def public_forwarder(self): 
        return self._settings['upstream'].get('public_forwarder', '8.8.8.8')
        
    @property
    def public_port(self):
        return self._settings['upstream'].get('public_port', 53)

    @property
    def enable_forwarding(self):
        return self._settings['upstream'].get('enable_forwarding', False)
    # --- Getters for "behavior" section ---
    @property
    def default_ttl(self): 
        return self._settings['behavior'].get('default_ttl', 60)
    
    @property
    def timeout(self): 
        return self._settings['behavior'].get('timeout', 2.0)
    
    # --- Getters for "cache" section ---
    @property
    def cache_file_path(self) -> str:
        """Returns the ABSOLUTE path to the cache file."""
        relative_path = self._settings['storage'].get('cache_file', 'dns_cache.pickle')
        # Join project root + relative path
        return str(self.project_root / relative_path)

    @property
    def save_interval(self) -> int:
        return self._settings['storage'].get('save_interval', 10)

    @property
    def cache_capacity(self) -> int:
        """Reads cache capacity from storage section, default 1000"""
        return self._settings['storage'].get('cache_capacity', 1000)