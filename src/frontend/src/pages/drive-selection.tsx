/** TMP  */
import { useState } from 'react';
import { Button } from "@openfun/cunningham-react";
import { Attachment } from '@/features/api/gen';

export type DriveFile = { url: string } & Omit<Attachment, 'sha256' | 'blobId'>;

/**
 * REMOVE THAT SHIT MAN AND USE THE REAL DRIVE WIDGET
 */
const DriveSelectionPage = () => {
    const [selectedFiles, setSelectedFiles] = useState<DriveFile[]>([]);
    
    const handleConfirm = () => {
        // Send data back to parent window
        window.opener?.postMessage({
            type: 'DRIVE_SELECTION_COMPLETE',
            files: selectedFiles,
            timestamp: new Date().toISOString()
        }, window.location.origin);
        
        // Close this window
        window.close();
    };
    
    const handleCancel = () => {
        // Close without sending data
        window.close();
    };
    
    const handleFileSelect = (file: DriveFile) => {
        setSelectedFiles(prev => {
            const isSelected = prev.some(f => f.id === file.id);
            if (isSelected) {
                return prev.filter(f => f.id !== file.id);
            } else {
                return [...prev, file];
            }
        });
    };
    
    // Mock file list - replace with your actual drive integration
    const availableFiles: DriveFile[] = [
        { id: '0', name: 'Document1.pdf', url: '/files/doc1.pdf', type: 'application/pdf', size: 1298321, created_at: new Date().toISOString() },
        { id: '1', name: 'Image1.png', url: '/files/image1.png', type: 'image/png', size: 34783, created_at: new Date().toISOString() },
    ];
    
    return (
        <div style={{ padding: '20px' }}>
            <h2>Select Drive Files</h2>
            
            <div style={{ marginBottom: '20px', maxHeight: '400px', overflowY: 'auto' }}>
                {availableFiles.map(file => (
                    <div 
                        key={file.id}
                        style={{
                            padding: '10px',
                            border: '1px solid #ccc',
                            margin: '5px 0',
                            cursor: 'pointer',
                            backgroundColor: selectedFiles.some(f => f.id === file.id) ? '#e3f2fd' : 'white'
                        }}
                        onClick={() => handleFileSelect(file)}
                    >
                        <input 
                            type="checkbox" 
                            checked={selectedFiles.some(f => f.id === file.id)}
                            onChange={() => {}} // Handled by parent div click
                        />
                        <span style={{ marginLeft: '10px' }}>{file.name}</span>
                    </div>
                ))}
            </div>
            
            <div style={{ display: 'flex', gap: '10px' }}>
                <Button onClick={handleCancel}>
                    Cancel
                </Button>
                <Button 
                    onClick={handleConfirm} 
                    disabled={selectedFiles.length === 0}
                    color="primary"
                >
                    Select Files ({selectedFiles.length})
                </Button>
            </div>
        </div>
    );
};

export default DriveSelectionPage; 
