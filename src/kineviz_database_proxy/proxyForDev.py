"""
Development Proxy Configuration Module
Auto-detect proxy settings and intercept all Python network requests
"""

import os
import socket
import urllib.parse
import urllib.request
import urllib.error
from typing import List, Optional, Dict, Any
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ProxyConfig:
    """Proxy configuration class"""
    
    def __init__(self):
        self.http_proxy = os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY')
        self.https_proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
        self.no_proxy = os.environ.get('no_proxy') or os.environ.get('NO_PROXY')
        
        # List of domains that need proxy (supports wildcards)
        self.proxy_domains = [
            'googleapis.com',
            '*.googleapis.com',
            'google.com',
            '*.google.com',
            'accounts.google.com',
            'oauth2.googleapis.com',
            'www.googleapis.com',
            'cloudresourcemanager.googleapis.com',
            'spanner.googleapis.com',
            # Add more domains that need proxy
        ]
        
        # List of domains that don't need proxy
        self.no_proxy_domains = []
        if self.no_proxy:
            self.no_proxy_domains = [domain.strip() for domain in self.no_proxy.split(',')]
        
        # Default domains that don't need proxy
        self.no_proxy_domains.extend([
            'localhost',
            '127.0.0.1',
            '::1',
            '.local'
        ])
    
    def should_use_proxy(self, url: str) -> bool:
        """Check if URL needs to use proxy"""
        if not (self.http_proxy or self.https_proxy):
            return False
        
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname or parsed.netloc
        
        # Check if it's in the no-proxy domain list
        for no_proxy_domain in self.no_proxy_domains:
            if self._match_domain(hostname, no_proxy_domain):
                logger.debug(f"Skip proxy (no_proxy): {hostname}")
                return False
        
        # Check if it's in the proxy domain list
        for proxy_domain in self.proxy_domains:
            if self._match_domain(hostname, proxy_domain):
                logger.debug(f"Use proxy: {hostname}")
                return True
        
        # Default: don't use proxy
        return False
    
    def _match_domain(self, hostname: str, pattern: str) -> bool:
        """Match domain pattern with wildcard support"""
        if not hostname:
            return False
            
        if pattern.startswith('*.'):
            # Wildcard matching
            suffix = pattern[2:]
            return hostname.endswith(suffix) or hostname == suffix
        else:
            # Exact matching
            return hostname == pattern
    
    def get_proxy_url(self, scheme: str) -> Optional[str]:
        """Get proxy URL for corresponding protocol"""
        if scheme.lower() == 'https':
            return self.https_proxy or self.http_proxy
        elif scheme.lower() == 'http':
            return self.http_proxy
        return None

# Global proxy configuration instance
proxy_config = ProxyConfig()

# Backup of original network functions
_original_urlopen = urllib.request.urlopen
_original_socket_create_connection = socket.create_connection

def _patched_urlopen(url, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs):
    """Proxy-intercepted urlopen function"""
    
    # If it's a string URL, check if proxy is needed
    if isinstance(url, str):
        if proxy_config.should_use_proxy(url):
            parsed = urllib.parse.urlparse(url)
            proxy_url = proxy_config.get_proxy_url(parsed.scheme)
            
            if proxy_url:
                logger.info(f"Accessing via proxy: {url}")
                logger.debug(f"Using proxy: {proxy_url}")
                
                # Create proxy handler
                proxy_handler = urllib.request.ProxyHandler({
                    'http': proxy_url,
                    'https': proxy_url
                })
                opener = urllib.request.build_opener(proxy_handler)
                
                # Create request
                if isinstance(url, str):
                    request = urllib.request.Request(url, data=data)
                else:
                    request = url
                    
                return opener.open(request, timeout=timeout)
    
    # For other cases, use original function
    return _original_urlopen(url, data=data, timeout=timeout, **kwargs)

def _patched_socket_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Proxy-intercepted socket connection function"""
    
    # Check if proxy is needed
    if len(address) >= 2:
        hostname, port = address[0], address[1]
        
        # Construct URL for checking
        scheme = 'https' if port == 443 else 'http'
        url = f"{scheme}://{hostname}:{port}"
        
        if proxy_config.should_use_proxy(url):
            proxy_url = proxy_config.get_proxy_url(scheme)
            if proxy_url:
                logger.debug(f"Socket connection via proxy: {hostname}:{port}")
                # Note: This may require more complex proxy socket implementation
                # For simple cases, we let it fall back to original connection
    
    return _original_socket_create_connection(address, timeout, source_address)

def setup_requests_proxy():
    """Set up proxy for requests library"""
    try:
        import requests
        
        # Get or create default session
        if not hasattr(requests, '_original_get'):
            requests._original_get = requests.get
            requests._original_post = requests.post
            requests._original_put = requests.put
            requests._original_delete = requests.delete
            requests._original_patch = requests.patch
            
        def _proxy_request(method, url, **kwargs):
            """Proxy request wrapper"""
            if proxy_config.should_use_proxy(url):
                proxy_url = proxy_config.get_proxy_url(urllib.parse.urlparse(url).scheme)
                if proxy_url:
                    if 'proxies' not in kwargs:
                        kwargs['proxies'] = {}
                    kwargs['proxies'].update({
                        'http': proxy_url,
                        'https': proxy_url
                    })
                    logger.info(f"Requests accessing via proxy: {url}")
            
            # Call original method
            original_method = getattr(requests, f'_original_{method}')
            return original_method(url, **kwargs)
        
        # Wrap requests methods
        requests.get = lambda url, **kwargs: _proxy_request('get', url, **kwargs)
        requests.post = lambda url, **kwargs: _proxy_request('post', url, **kwargs)
        requests.put = lambda url, **kwargs: _proxy_request('put', url, **kwargs)
        requests.delete = lambda url, **kwargs: _proxy_request('delete', url, **kwargs)
        requests.patch = lambda url, **kwargs: _proxy_request('patch', url, **kwargs)
        
        logger.info("Proxy interception set up for requests library")
        
    except ImportError:
        logger.debug("requests library not installed, skipping setup")

def setup_google_cloud_proxy():
    """Set up proxy for Google Cloud client libraries"""
    try:
        # Set Google Cloud related environment variables
        if proxy_config.http_proxy:
            os.environ.setdefault('GOOGLE_CLOUD_PROXY', proxy_config.http_proxy)
            os.environ.setdefault('grpc_proxy', proxy_config.http_proxy)
            os.environ.setdefault('GRPC_PROXY', proxy_config.http_proxy)
            
        logger.info("Proxy environment variables set for Google Cloud clients")
        
    except Exception as e:
        logger.warning(f"Error setting up Google Cloud proxy: {e}")

def install_proxy_interceptor():
    """Install proxy interceptor"""
    if not (proxy_config.http_proxy or proxy_config.https_proxy):
        logger.info("No proxy settings detected, skipping proxy interceptor installation")
        return False
    
    logger.info("Proxy settings detected, installing proxy interceptor...")
    logger.info(f"HTTP proxy: {proxy_config.http_proxy}")
    logger.info(f"HTTPS proxy: {proxy_config.https_proxy}")
    logger.info(f"Domains requiring proxy: {proxy_config.proxy_domains}")
    logger.info(f"Domains not requiring proxy: {proxy_config.no_proxy_domains}")
    
    # Install urllib interceptor
    urllib.request.urlopen = _patched_urlopen
    
    # Install socket interceptor (optional, may affect performance)
    # socket.create_connection = _patched_socket_create_connection
    
    # Install requests interceptor
    setup_requests_proxy()
    
    # Set up Google Cloud proxy
    setup_google_cloud_proxy()
    
    logger.info("Proxy interceptor installation complete!")
    return True

def uninstall_proxy_interceptor():
    """Uninstall proxy interceptor"""
    logger.info("Uninstalling proxy interceptor...")
    
    # Restore original functions
    urllib.request.urlopen = _original_urlopen
    socket.create_connection = _original_socket_create_connection
    
    # Restore requests original methods
    try:
        import requests
        if hasattr(requests, '_original_get'):
            requests.get = requests._original_get
            requests.post = requests._original_post
            requests.put = requests._original_put
            requests.delete = requests._original_delete
            requests.patch = requests._original_patch
            
    except ImportError:
        pass
    
    logger.info("Proxy interceptor uninstallation complete!")

def test_proxy_connection():
    """Test proxy connection"""
    test_urls = [
        'https://www.googleapis.com',
        'https://accounts.google.com',
        'https://www.google.com'
    ]
    
    for url in test_urls:
        try:
            logger.info(f"Testing connection: {url}")
            
            if proxy_config.should_use_proxy(url):
                logger.info(f"  -> Will use proxy")
            else:
                logger.info(f"  -> Direct connection")
                
            response = urllib.request.urlopen(url, timeout=10)
            logger.info(f"  -> Success (status code: {response.getcode()})")
            
        except Exception as e:
            logger.error(f"  -> Failed: {e}")

# Auto-install proxy interceptor
if __name__ == "__main__":
    install_proxy_interceptor()
    test_proxy_connection()
else:
    # Auto-install when imported as module
    install_proxy_interceptor()