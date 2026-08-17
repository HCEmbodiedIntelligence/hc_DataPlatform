{{- define "hc-data-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "hc-data-platform.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "hc-data-platform.name" . }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "hc-data-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "hc-data-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "hc-data-platform.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "hc-data-platform.selectorLabels" . }}
app.kubernetes.io/version: {{ .Values.global.releaseId | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
hc-data-platform.io/release-id: {{ .Values.global.releaseId | quote }}
{{- end }}

{{- define "hc-data-platform.image" -}}
{{- $repository := required "frontend.image.repository is required" .repository -}}
{{- $digest := required "frontend.image.digest is required" .digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $digest) -}}
{{- fail "frontend.image.digest must be a sha256 digest" -}}
{{- end -}}
{{- printf "%s@%s" $repository $digest -}}
{{- end }}

{{- define "hc-data-platform.backendImage" -}}
{{- $repository := required "backend image.repository is required" .repository -}}
{{- $digest := required "backend image.digest is required" .digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $digest) -}}
{{- fail "backend image.digest must be a sha256 digest" -}}
{{- end -}}
{{- printf "%s@%s" $repository $digest -}}
{{- end }}

{{- define "hc-data-platform.backendFullname" -}}
{{- printf "%s-backend" (include "hc-data-platform.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "hc-data-platform.backendServiceAccountName" -}}
{{- if .Values.backend.serviceAccount.create }}
{{- default (include "hc-data-platform.backendFullname" .) .Values.backend.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.backend.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "hc-data-platform.backendSecretEnv" -}}
- name: HC_POSTGRES_DSN
  valueFrom:
    secretKeyRef:
      name: {{ required "backend.existingSecrets.postgres.name is required" .Values.backend.existingSecrets.postgres.name | quote }}
      key: {{ required "backend.existingSecrets.postgres.dsnKey is required" .Values.backend.existingSecrets.postgres.dsnKey | quote }}
- name: HC_OBJECT_STORE_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ required "backend.existingSecrets.objectStore.name is required" .Values.backend.existingSecrets.objectStore.name | quote }}
      key: {{ required "backend.existingSecrets.objectStore.accessKeyKey is required" .Values.backend.existingSecrets.objectStore.accessKeyKey | quote }}
- name: HC_OBJECT_STORE_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ required "backend.existingSecrets.objectStore.name is required" .Values.backend.existingSecrets.objectStore.name | quote }}
      key: {{ required "backend.existingSecrets.objectStore.secretKeyKey is required" .Values.backend.existingSecrets.objectStore.secretKeyKey | quote }}
- name: HC_CURSOR_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ required "backend.existingSecrets.application.name is required" .Values.backend.existingSecrets.application.name | quote }}
      key: {{ required "backend.existingSecrets.application.cursorSecretKey is required" .Values.backend.existingSecrets.application.cursorSecretKey | quote }}
{{- end }}
