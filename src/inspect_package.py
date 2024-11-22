import importlib
import sys

def inspect_package(package_name):
    """
    Inspect a Python package's details.
    
    Args:
        package_name (str): Name of the package to inspect
    """
    try:
        # Import the package
        package = importlib.import_module(package_name)
        
        # Basic package information
        print(f"Package: {package_name}")
        print(f"Package Path: {package.__file__ if hasattr(package, '__file__') else 'Not available'}")
        
        # Package version (if available)
        try:
            print(f"Version: {package.__version__}")
        except AttributeError:
            print("Version: Not found")
        
        # Inspect package contents
        print("\nPackage Contents:")
        for item in dir(package):
            print(f"  {item}")
        
    except ImportError:
        print(f"Could not import package: {package_name}")
    except Exception as e:
        print(f"Error inspecting package: {e}")

inspect_package ("fitz")        