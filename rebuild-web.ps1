# Rebuild and restart the web container
Write-Host "Building web container..." -ForegroundColor Cyan
cd "$PSScriptRoot\docker"
docker-compose build observal-web
if ($LASTEXITCODE -eq 0) {
    Write-Host "Starting web container..." -ForegroundColor Cyan
    docker-compose up -d observal-web
    Write-Host "Done! Web UI should be available at http://localhost" -ForegroundColor Green
} else {
    Write-Host "Build failed!" -ForegroundColor Red
}