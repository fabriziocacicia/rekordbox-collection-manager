import Foundation

enum TursoConfig {
    static let defaultDatabaseURL = "https://rekordbox-companion-fabriziocacicia.aws-eu-west-1.turso.io"
    static let defaultAuthToken = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTAzMjkzNTMsImlkIjoiMDFhMGQ3ZjEtOWYwMS03Zjk4LTkzNDctODkzYzQyZDlhYjgwIiwia2lkIjoiaDg1cUEzcGMza3dFalFUZmFXUndGalg3ZzZNTVJtUW56RU5tOTZOV20xMCIsInJpZCI6IjJiZTc5YjVhLWI5ZjctNDExYS05YzI3LTdhYjUzZGQ5MjdkNyJ9.zDmQdNRdpBOiGlWrWS1IH1axLmnFFf0WIiHrw_7tLUkb9HDPojw_8kSU_zSf7YvYblgXnYFJHNiFY4h--qaSCg"

    private static let urlKey = "turso_database_url"
    private static let tokenKey = "turso_auth_token"

    static var databaseURL: String {
        get {
            UserDefaults.standard.string(forKey: urlKey) ?? defaultDatabaseURL
        }
        set {
            UserDefaults.standard.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: urlKey)
        }
    }

    static var authToken: String {
        get {
            UserDefaults.standard.string(forKey: tokenKey) ?? defaultAuthToken
        }
        set {
            UserDefaults.standard.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: tokenKey)
        }
    }

    static func resetToDefaults() {
        UserDefaults.standard.removeObject(forKey: urlKey)
        UserDefaults.standard.removeObject(forKey: tokenKey)
    }
}
