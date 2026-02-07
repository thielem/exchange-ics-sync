# Exchange ICS Sync

A Python service that connects to your Exchange on-premise server, fetches calendar events, and serves them as an ICS feed via HTTP.

## Features

- **All-day Events**: Properly handles all-day events
- **Recurring Events**: Converts Exchange recurrence patterns to iCalendar RRULE format
- **Timezone Support**: Maintains timezone information for accurate event times
- **Configurable URL Paths**: Customize the ICS feed URL path to your preference
- **Auto-sync**: Periodically syncs calendar data in the background
- **Docker Ready**: Easy deployment via Docker Compose

## Requirements

- Python 3.11+
- Docker and Docker Compose (for containerized deployment)
- Access to an Exchange on-premise server
- Valid Exchange credentials

## Quick Start with Docker Compose

1. **Clone the repository**
   ```bash
   git clone https://github.com/thielem/exchange-ics-sync
   cd exchange-ics-sync
   ```

2. **Configure the service**

  **Token**
  Generate a token using e.g. `openssl rand -base64 64 | tr '+/' '-_' | tr -d '=' | tr -d '\n'`.
  The token can be any random string and is meant to keep your calendar deployment secure. It must be passed as a URL query parameter. Any request without a valid token will return `404`.

   Create and edit `config.yaml` with your Exchange server details:
   ```yaml
   exchange:
     server: "mail.example.com"
     email: "user@example.com"
     username: "username"
     password: "your-password"
     auth_type: "NTLM"

   calendar:
     name: "my-calendar"
     days_past: 30
     days_future: 365
     sync_interval_minutes: 15

   server:
     host: "0.0.0.0"
     port: 8080
     calendar_url_path: "/cal/{calendar_name}.ics"
     token: "my-secure-token"
   ```

   If you wish to store secrets in environment variables instead, just configure an empty string in the yaml file. Environment variables will overwrite the `config.yaml` values.

1. **Start the service**
   ```bash
   docker-compose up -d
   ```

2. **Access your calendar**

   The ICS feed will be available at:
   ```
   http://localhost:8080/cal/my-calendar.ics?token=my-secure-token
   ```

## Configuration

### config.yaml

The main configuration file with the following sections:

#### Exchange Settings

- `server`: Exchange server address (e.g., mail.example.com)
- `email`: Email address of the calendar account
- `username`: Username for authentication
- `password`: Password for authentication
- `auth_type`: Authentication type (NTLM, Basic, or Digest)

#### Calendar Settings

- `name`: Calendar identifier used in the URL
- `days_past`: Number of days in the past to fetch events (default: 30)
- `days_future`: Number of days in the future to fetch events (default: 365)
- `sync_interval_minutes`: How often to sync with Exchange (default: 15)

#### Server Settings

- `host`: Host to bind to (use 0.0.0.0 for all interfaces)
- `port`: Port to listen on (default: 8080)
- `calendar_url_path`: URL path pattern for the ICS feed
  - Use `{calendar_name}` as a placeholder for the calendar name
  - Examples:
    - `/cal/{calendar_name}.ics` → `http://server:port/cal/my-calendar.ics`
    - `/{calendar_name}.ics` → `http://server:port/my-calendar.ics`
    - `/calendars/{calendar_name}` → `http://server:port/calendars/my-calendar`
- `token`: URL query parameter that is used to authenticate requests to the calendar.
- `secure_healthcheck`: Whether the token must be passed as a Bearer when calling `/health`. This is recommended to avoid information leakage via the health endpoint. If enabled, any request with invalid token will return `404`.

### Environment Variables

You can override configuration values using environment variables:

- `EXCHANGE_SERVER`
- `EXCHANGE_EMAIL`
- `EXCHANGE_USERNAME`
- `EXCHANGE_PASSWORD`
- `EXCHANGE_DOMAIN`
- `SERVER_HOST`
- `SERVER_PORT`
- `CONFIG_PATH`

To use environment variables with Docker Compose, uncomment and set them in `docker-compose.yml`.

## Manual Installation (without Docker)

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure the service**

   Edit `config.yaml` with your settings.

3. **Run the service**
   ```bash
   python app.py
   ```

## API Endpoints

### GET /health

Health check endpoint for monitoring. 

**Response:**
```json
{
  "status": "healthy",
  "last_sync": "2024-01-15T10:30:00+00:00"
}
```

### GET /cal/{calendar-name}.ics

Serves the ICS calendar file (path configurable via `calendar_url_path`).

**Response:** ICS file with calendar events

## Usage with Calendar Applications

### Apple Calendar

1. Open Calendar app
2. File → New Calendar Subscription
3. Enter the calendar URL: `http://your-server:8080/cal/my-calendar.ics`
4. Set refresh frequency to match your sync interval

### Google Calendar

1. Open Google Calendar
2. Click "+" next to "Other calendars"
3. Select "From URL"
4. Enter the calendar URL: `http://your-server:8080/cal/my-calendar.ics`

### Outlook

1. Open Outlook
2. File → Account Settings → Internet Calendars
3. Click "New"
4. Enter the calendar URL: `http://your-server:8080/cal/my-calendar.ics`

## Docker Deployment

### Building the Image

```bash
docker build -t exchange-ics-sync .
```

### Running with Docker

```bash
docker run -d \
  -p 8080:8080 \
  -v $(pwd)/config.yaml:/app/config/config.yaml:ro \
  --name exchange-ics-sync \
  exchange-ics-sync
```

### Using Docker Compose

```bash
# Start the service
docker-compose up -d

# Pull the latest image
docker-compose pull

# View logs
docker-compose logs -f

# Stop the service
docker-compose down

# Restart after pulling a new image
docker-compose up -d
```

## Troubleshooting

### Connection Issues

If you can't connect to Exchange:

1. Verify server address and credentials in `config.yaml`
2. Check if the Exchange server is accessible from your network
3. Try different authentication types (NTLM, Basic, Digest)
4. Check the logs: `docker-compose logs -f`

### Calendar Not Syncing

1. Check the `/health` endpoint for last sync time
2. Review logs for error messages
3. Verify Exchange credentials have calendar access permissions
4. Ensure firewall rules allow outbound connections to Exchange server

### Empty Calendar Feed

1. Verify date range settings (`days_past` and `days_future`)
2. Check if the Exchange calendar actually has events in the date range
3. Review logs for filtering or fetch errors

## Security Considerations

- **Password Security**: Never commit `config.yaml` with real credentials to version control
- **HTTPS**: Use a reverse proxy (nginx, Traefik) to add HTTPS for production
- **Network Access**: Restrict access to the service using firewall rules
- **Credentials**: Use environment variables or secrets management for sensitive data

## Development

### Project Structure

```
exchange-ics-sync/
├── app.py                 # Main application
├── config.yaml            # Configuration file
├── requirements.txt       # Python dependencies
├── Dockerfile             # Docker image definition
├── docker-compose.yml     # Docker Compose configuration
├── .env.example           # Environment variables template
└── README.md              # This file
```

### Running Tests

```bash
# Install dev dependencies
pip install -r requirements.txt

# Run the application in debug mode
python app.py
```

## License

MIT License - feel free to use and modify as needed.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Support

For issues and questions, please open an issue on the GitHub repository.
