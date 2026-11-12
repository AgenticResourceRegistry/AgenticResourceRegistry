#!/usr/bin/env python3
"""
Update all files to use HarnessLogoFixed instead of HarnessLogo
to avoid CSP issues with @lobehub/icons
"""

import os
import re
from pathlib import Path

def update_file(filepath: Path):
    """Update a single file to use HarnessLogoFixed"""
    try:
        content = filepath.read_text()
        
        # Check if file uses HarnessLogo
        if "HarnessLogo" in content:
            print(f"Updating {filepath}")
            
            # Replace imports
            if "from \"@/components/registry/harness-logo\"" in content:
                content = content.replace(
                    "from \"@/components/registry/harness-logo\"",
                    "from \"@/components/registry/harness-logo-fixed\""
                )
            elif "from '@/components/registry/harness-logo'" in content:
                content = content.replace(
                    "from '@/components/registry/harness-logo'",
                    "from '@/components/registry/harness-logo-fixed'"
                )
            
            # Replace component name
            content = content.replace("HarnessLogo(", "HarnessLogoFixed(")
            content = content.replace("HarnessLogo ", "HarnessLogoFixed ")
            content = content.replace("HarnessLogo}", "HarnessLogoFixed}")
            content = content.replace("HarnessLogo:", "HarnessLogoFixed:")
            content = content.replace("HarnessLogo>", "HarnessLogoFixed>")
            
            # Replace resolveHarnessSlug
            content = content.replace("resolveHarnessSlug", "resolveHarnessSlugFixed")
            
            filepath.write_text(content)
            return True
    except Exception as e:
        print(f"Error updating {filepath}: {e}")
    
    return False

def main():
    web_dir = Path("s:/Observal_/web")
    
    # Files that use HarnessLogo (from git diff)
    files_to_update = [
        "src/components/registry/harness-badges.tsx",
        "src/components/builder/model-picker.tsx",
        "src/components/builder/preview-panel.tsx",
        "src/components/registry/component-install-command.tsx",
        "src/components/registry/pull-command.tsx",
        "src/components/registry/submit-component-dialog.tsx",
        "src/pages/admin/settings/schema-section.tsx",
        "src/pages/user/traces/index.tsx",
        "src/components/registry/harness-logo.tsx",  # Update import in this file too
    ]
    
    updated_count = 0
    for file_rel in files_to_update:
        filepath = web_dir / file_rel
        if filepath.exists():
            if update_file(filepath):
                updated_count += 1
        else:
            print(f"File not found: {filepath}")
    
    print(f"\nUpdated {updated_count} files")
    print("\nNext steps:")
    print("1. Remove @lobehub/icons from package.json")
    print("2. Update pnpm-lock.yaml: cd s:\\Observal_\\web && pnpm remove @lobehub/icons")
    print("3. Delete the old harness-logo.tsx file")
    print("4. Rename harness-logo-fixed.tsx to harness-logo.tsx")
    print("5. Rebuild web container")

if __name__ == "__main__":
    main()