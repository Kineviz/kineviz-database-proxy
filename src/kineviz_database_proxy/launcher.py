# -*- coding: utf-8 -*-
"""
Launcher script to ensure proper encoding environment
"""

import sys
import os
import locale

def setup_encoding():
    """Set up proper encoding environment"""
    
    # Set environment variables
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'
    
    # Completely disable OpenTelemetry SDK to prevent metrics export errors
    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ["OTEL_METRICS_EXPORTER"] = "none"
    os.environ["OTEL_TRACES_EXPORTER"] = "none"
    os.environ["OTEL_LOGS_EXPORTER"] = "none"
    os.environ["SPANNER_ENABLE_BUILT_IN_METRICS"] = "false"
    os.environ["SPANNER_ENABLE_EXTENDED_TRACING"] = "false"
    os.environ["SPANNER_ENABLE_METRICS"] = "false"
    os.environ["GOOGLE_CLOUD_DISABLE_METRICS"] = "true"
    
    # Windows-specific handling
    if sys.platform == "win32":
        try:
            # Set console code page to UTF-8
            os.system('chcp 65001 > nul')
            
            # Set locale
            locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
        except:
            try:
                locale.setlocale(locale.LC_ALL, 'C.UTF-8')
            except:
                pass
        
        # Redirect standard output to support UTF-8
        try:
            import codecs
            sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
            sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())
        except:
            pass

def main():
    """Main launcher function"""
    setup_encoding()
    
    # Import and run the main application
    try:
        from .main import main as app_main
        app_main()
    except ImportError:
        # If running this file directly
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from src.kineviz_database_proxy.main import main as app_main
        app_main()

if __name__ == "__main__":
    main()