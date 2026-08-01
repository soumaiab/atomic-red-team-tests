# === CONFIGURATION ===
$startTime = "2026-08-01T20:00:00.000Z"
$endTime   = "2026-08-01T21:00:00.000Z"
$agentName = "windows-10"
$size = 10000
$index = 1
$lastSort = $null
$baseUrl = "http://localhost:9200/logs-*/_search"

# === QUERY LOOP ===
do {
    Write-Host "Fetching page $index..."

    # Construct request body
    $body = @{
        size  = $size
        sort  = @(
            @{ "@timestamp" = "asc" },
            @{ "_doc" = "asc" }
        )
        query = @{
            bool = @{
                must = @(
                    @{
                        range = @{
                            "@timestamp" = @{
                                gte = $startTime
                                lt  = $endTime
                            }
                        }
                    },
                    @{
                        term = @{
                            "agent.name.keyword" = $agentName
                        }
                    }
                )
            }
        }
    }

    if ($lastSort) {
        $body["search_after"] = $lastSort
    }

    $nindex = $index + 0

    $jsonBody = $body | ConvertTo-Json -Compress -Depth 10
    $outputFile = "page-$nindex.json"

    # Send request and save response
    Invoke-RestMethod -Uri $baseUrl -Method POST -ContentType "application/json" -Body $jsonBody -OutFile $outputFile

    # Load last sort values from last hit
    $response = Get-Content $outputFile -Raw | ConvertFrom-Json
    $hits = $response.hits.hits

    if ($hits.Count -gt 0) {
        $lastSort = $hits[-1].sort
        $index++
    }

} while ($hits.Count -gt 0)

Write-Host "Done. Retrieved $($index - 1) pages."
