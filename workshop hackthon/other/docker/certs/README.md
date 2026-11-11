# Extra trusted CA certificates (optional)

Leave this folder empty unless the Docker build fails with
`CERTIFICATE_VERIFY_FAILED` / `unable to get local issuer certificate`.

That error means something on your machine (antivirus HTTPS scanning such as Avast or
Kaspersky, or a corporate proxy) re-signs HTTPS traffic with its own root certificate.
Windows trusts that root, but the containers do not.

Fix (TLS verification stays on):

1. Export that root certificate in PEM/Base-64 format, e.g. in PowerShell:

   ```powershell
   $c = Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like "*Avast*" | Select-Object -First 1
   "-----BEGIN CERTIFICATE-----`n$([Convert]::ToBase64String($c.RawData,'InsertLineBreaks'))`n-----END CERTIFICATE-----" |
     Set-Content other\docker\certs\local-root-ca.crt -Encoding ascii
   ```

2. Re-run `docker compose build`. Both Dockerfiles add every `*.crt` in this folder
   to the trust store used by pip (backend) and npm (frontend).

Alternatively, disable HTTPS scanning in the antivirus while building.
Certificates placed here are local to your machine and are git-ignored.
