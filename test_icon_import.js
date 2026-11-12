// Test to check if @lobehub/icons imports work
const path = require('path');
const fs = require('fs');

console.log('Testing @lobehub/icons import...');

const webPath = path.join(__dirname, 'web');
const nodeModulesPath = path.join(webPath, 'node_modules', '@lobehub', 'icons');

// Check if package exists
if (!fs.existsSync(nodeModulesPath)) {
  console.error('❌ @lobehub/icons not found in node_modules');
  console.log('Try running: cd web && pnpm install');
  process.exit(1);
}

console.log('✅ @lobehub/icons package found');

// Check for specific icons
const iconsToCheck = [
  'ClaudeCode',
  'Kiro', 
  'Cursor',
  'Pi',
  'Codex',
  'GithubCopilot',
  'OpenCode',
  'Antigravity',
  'Goose'
];

const esPath = path.join(nodeModulesPath, 'es');
const iconsPath = path.join(nodeModulesPath, 'lib', 'icons');

console.log('\nChecking icon availability:');
for (const icon of iconsToCheck) {
  const esIconPath = path.join(esPath, icon, 'index.mjs');
  const libIconPath = path.join(iconsPath, `${icon}.js`);
  
  if (fs.existsSync(esIconPath)) {
    console.log(`  ✅ ${icon} found in es/`);
  } else if (fs.existsSync(libIconPath)) {
    console.log(`  ✅ ${icon} found in lib/icons/`);
  } else {
    console.log(`  ❌ ${icon} not found`);
    
    // List what's actually in the directories
    try {
      const esDir = path.join(esPath, icon);
      if (fs.existsSync(esDir)) {
        console.log(`    Contents of ${esDir}:`);
        fs.readdirSync(esDir).forEach(file => console.log(`      - ${file}`));
      }
    } catch (err) {
      // ignore
    }
  }
}

console.log('\n✅ Icon import test complete');