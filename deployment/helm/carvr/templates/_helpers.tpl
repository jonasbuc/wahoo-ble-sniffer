{{/*
_helpers.tpl – shared template helpers for the CarVR Helm chart.
*/}}

{{/*
Expand the name of the chart.
*/}}
{{- define "carvr.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "carvr.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "carvr.labels" -}}
app.kubernetes.io/part-of: carvr
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end }}

{{/*
Selector labels for a named component (pass component name as .component).
*/}}
{{- define "carvr.selectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Image reference helper.
Usage: {{ include "carvr.image" (dict "registry" .Values.global.imageRegistry "repo" .Values.analyticsApi.image.repository "tag" .Values.analyticsApi.image.tag) }}
*/}}
{{- define "carvr.image" -}}
{{- if .registry -}}
{{ .registry }}/{{ .repo }}:{{ .tag | default "latest" }}
{{- else -}}
{{ .repo }}:{{ .tag | default "latest" }}
{{- end }}
{{- end }}
