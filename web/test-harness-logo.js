// Quick test to verify @lobehub/icons imports work
const fs = require('fs');
const path = require('path');

// Check harness-logo.tsx file
const logoPath = path.join(__dirname, 'src/components/registry/harness-logo.tsx');
const content = fs.readFileSync(logoPath, 'utf8');

console.log('Checking harness-logo.tsx...');
console.log('Has @lobehub/icons imports:', content.includes('@lobehub/icons'));
console.log('Has ClaudeCode import:', content.includes('ClaudeCode'));
console.log('Has Kiro import:', content.includes('Kiro'));
console.log('Has Cursor import:', content.includes('Cursor'));
console.log('Has GithubCopilot import:', content.includes('GithubCopilot'));

// Check if the icons are actually available in node_modules
const nodeModulesPath = path.join(__dirname, 'node_modules/@lobehub/icons/es');
console.log('\nChecking if @lobehub/icons exists:', fs.existsSync(nodeModulesPath));

if (fs.existsSync(nodeModulesPath)) {
  console.log('ClaudeCode exists:', fs.existsSync(path.join(nodeModulesPath, 'ClaudeCode/index.mjs')));
  console.log('Kiro exists:', fs.existsSync(path.join(nodeModulesPath, 'Kiro/index.mjs')));
  console.log('Cursor exists:', fs.existsSync(path.join(nodeModulesPath, 'Cursor/index.mjs')));
  console.log('GithubCopilot exists:', fs.existsSync(path.join(nodeModulesPath, 'GithubCopilot/index.mjs')));
}