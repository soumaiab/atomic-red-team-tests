# === CONFIGURATION ===
$startTime = "2026-08-01T23:45:00.000Z"
$endTime   = "2026-08-02T00:40:00.000Z"
$agentName = "windows-15"
$size = 10000
$index = 1
$lastSort = $null
$esRoot = "http://localhost:9200"
$indexPattern = "logs-*"
$pitKeepAlive = "5m"

# === OPEN A POINT-IN-TIME ===
# search_after alone re-queries the LIVE index on every request. If documents get
# deleted/rolled over mid-export (ILM retention, index rotation, etc.), the total and
# match set can shrink between pages, silently dropping hits from the union of pages.
# A PIT freezes a consistent snapshot for the whole export instead.
#
# NOTE: this cluster is OpenSearch (confirmed via GET /), not Elasticsearch - its PIT
# API lives at /_search/point_in_time (not /_pit) and the id field in every request/
# response is "pit_id" (not "id"). Verified directly against this cluster.
Write-Host "Opening point-in-time..."
$pitResponse = Invoke-RestMethod -Uri "$esRoot/$indexPattern/_search/point_in_time?keep_alive=$pitKeepAlive" -Method POST
$pitId = $pitResponse.pit_id

try {
    # === QUERY LOOP ===
    do {
        Write-Host "Fetching page $index..."

        # Construct request body
        $body = @{
            size  = $size
            pit   = @{
                id         = $pitId
                keep_alive = $pitKeepAlive
            }
            # Elasticsearch's PIT wants "_shard_doc" as the tiebreaker, but this
            # OpenSearch cluster rejects it ("No mapping found for [_shard_doc]"). _doc
            # is only unique WITHIN a single shard, not across the many shards/indices
            # logs-* spans - using it as the tiebreaker let search_after return the same
            # document again on a later page whenever two docs across different shards
            # shared a _doc value, inflating the total. _id is globally unique across
            # every shard/index, confirmed to work as a sort field on this cluster.
            sort  = @(
                @{ "@timestamp" = "asc" },
                @{ "_id" = "asc" }
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

        # Send request and save response. Note: with a PIT, the request goes to the
        # generic /_search endpoint - no index name in the URL, since the PIT id
        # already pins which indices/shards to search.
        Invoke-RestMethod -Uri "$esRoot/_search" -Method POST -ContentType "application/json" -Body $jsonBody -OutFile $outputFile

        # Load last sort values from last hit. Force array context with @(...) - in
        # Windows PowerShell 5.1, ConvertFrom-Json unwraps a single-element JSON array
        # down to a bare object, which would make .Count silently $null (and the loop
        # below stop early) on any page that happens to return exactly one hit.
        $response = Get-Content $outputFile -Raw | ConvertFrom-Json
        $hits = @($response.hits.hits)

        if ($hits.Count -gt 0) {
            $lastSort = $hits[-1].sort
            $index++
        }

    } while ($hits.Count -gt 0)

    Write-Host "Done. Retrieved $($index - 1) pages."
} finally {
    # Always release the PIT, even if the loop above throws.
    if ($pitId) {
        Write-Host "Closing point-in-time..."
        Invoke-RestMethod -Uri "$esRoot/_search/point_in_time" -Method DELETE -ContentType "application/json" -Body (@{ pit_id = @($pitId) } | ConvertTo-Json) | Out-Null
    }
}
